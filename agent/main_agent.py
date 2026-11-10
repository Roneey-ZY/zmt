import asyncio
import json

from pydantic import BaseModel

from agent import tools
from agent.prompts import AUDIT_INSTRUCTIONS, QUESTION_INSTRUCTIONS
from core.schemas import Finding

DEFAULT_MODEL = "gpt-6-luna"
DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_URL = "https://api.deepseek.com"


def configured_model(setting):
    deepseek_key = setting("DEEPSEEK_API_KEY").strip()
    if deepseek_key:
        return "deepseek", deepseek_key, setting("DEEPSEEK_MODEL", DEEPSEEK_MODEL)
    return "openai", setting("OPENAI_API_KEY").strip(), setting("TRADECHECK_MODEL", DEFAULT_MODEL)


class Explanation(BaseModel):
    finding_id: str
    explanation: str


class AgentSummary(BaseModel):
    summary: str
    explanations: list[Explanation]


class AgentRunError(RuntimeError):
    pass


def public_api_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None)
    label = {
        400: "请求、模型或结构化输出配置不受支持",
        401: "API 凭据认证失败",
        403: "API 项目权限不足",
        404: "API 模型或资源不可用",
        429: "API 限流或配额不足",
    }.get(status, {
        "APIConnectionError": "服务器无法连接模型 API",
        "APITimeoutError": "模型 API 请求超时",
        "MaxTurnsExceeded": "模型未能在调用轮数上限内完成审核",
    }.get(type(exc).__name__, "调用未完成"))
    safe_codes = {"invalid_api_key", "insufficient_quota", "rate_limit_exceeded",
                  "model_not_found", "unsupported_value", "context_length_exceeded"}
    return f"{label}（{type(exc).__name__}" + (
        f"，HTTP {status}" if status else ""
    ) + (f"，{code}" if isinstance(code, str) and code in safe_codes else "") + "）"


def model_settings(provider, max_tokens):
    from agents import ModelSettings
    from openai.types.shared import Reasoning

    return ModelSettings(
        parallel_tool_calls=False, store=False, max_tokens=max_tokens,
        reasoning=Reasoning(effort="none") if provider == "deepseek" else None,
    )


def model_client(key, provider):
    from openai import AsyncOpenAI

    options = {"base_url": DEEPSEEK_URL} if provider == "deepseek" else {}
    return AsyncOpenAI(api_key=key, timeout=30, max_retries=0, **options)


def make_agent(model, provider="openai"):
    from agents import Agent, RunContextWrapper, function_tool

    # DeepSeek can issue parallel calls even when parallel_tool_calls is false.
    # Expose only the tool whose inputs exist in this turn.
    def ready(predicate):
        if provider != "deepseek":
            return True
        return lambda context, _agent: predicate(context.context)

    @function_tool(is_enabled=ready(lambda run: run.documents is None))
    async def extract_document_fields(context: RunContextWrapper[tools.ReviewRun]) -> str:
        """Extract structured fields and evidence locations from the three uploaded documents."""
        return context.context.execute(tools.extract_document_fields)

    @function_tool(is_enabled=ready(lambda run: run.documents is not None and run.normalized is None))
    async def normalize_trade_data(context: RunContextWrapper[tools.ReviewRun]) -> str:
        """Normalize the extracted fields into typed trade data."""
        return context.context.execute(tools.normalize_trade_data)

    @function_tool(is_enabled=ready(lambda run: run.normalized is not None and run.result is None))
    async def audit_document_consistency(context: RunContextWrapper[tools.ReviewRun]) -> str:
        """Audit the normalized documents and return findings with limited evidence."""
        run = context.context
        summary = run.execute(tools.audit_document_consistency)
        return json.dumps({
            "execution": summary,
            "findings": [tools.model_evidence(f, run.language) for f in run.result.findings[:20]],
        }, ensure_ascii=False)

    @function_tool(is_enabled=ready(lambda run: run.result is not None and run.report is None))
    async def export_audit_report(context: RunContextWrapper[tools.ReviewRun]) -> str:
        """Export the completed audit as an Excel report in the chosen language."""
        return context.context.execute(tools.export_audit_report)

    return Agent(
        name="TradeCheck",
        model=model,
        instructions=AUDIT_INSTRUCTIONS,
        output_type=AgentSummary,
        tools=[extract_document_fields, normalize_trade_data, audit_document_consistency, export_audit_report],
        model_settings=model_settings(provider, 1600),
    )


async def _audit(run, key, model_name, request, provider):
    from agents import OpenAIResponsesModel, RunConfig, Runner

    async with model_client(key, provider) as client:
        agent = make_agent(OpenAIResponsesModel(model=model_name, openai_client=client), provider)
        result = await Runner.run(
            agent, f"Complete the audit and export Excel. Language: {run.language}. "
            f"User focus: {request[:350]}", context=run,
            max_turns=10, run_config=RunConfig(tracing_disabled=True),
        )
    if run.report is None or run.result is None:
        raise AgentRunError("模型结束时未生成完整审核报告。请查看实际工具调用日志。")
    ids = {f.id for f in run.result.findings[:20]}
    output = result.final_output
    output.explanations = [x for x in output.explanations if x.finding_id in ids]
    return output


def run_agent(run: tools.ReviewRun, key: str, model_name: str = DEFAULT_MODEL,
              request: str = "", *, provider: str = "openai") -> AgentSummary:
    if not key:
        raise AgentRunError("服务器尚未配置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY。")
    try:
        return asyncio.run(_audit(run, key, model_name, request, provider))
    except AgentRunError:
        raise
    except Exception as exc:
        raise AgentRunError(public_api_error(exc)) from exc


async def _question(finding, question, key, model_name, provider):
    from agents import Agent, OpenAIResponsesModel, RunConfig, Runner

    async with model_client(key, provider) as client:
        agent = Agent(name="TradeCheck", instructions=QUESTION_INSTRUCTIONS,
                      model=OpenAIResponsesModel(model=model_name, openai_client=client),
                      model_settings=model_settings(provider, 400))
        prompt = json.dumps({"finding": tools.model_evidence(finding, "zh"),
                             "question": question[:350]}, ensure_ascii=False)
        response = await Runner.run(agent, prompt, max_turns=2,
                                    run_config=RunConfig(tracing_disabled=True))
    return response.final_output


def ask_finding(finding: Finding, question: str, key: str,
                model_name: str = DEFAULT_MODEL, *, provider: str = "openai") -> str:
    if not key:
        raise AgentRunError("服务器尚未配置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY。")
    try:
        return asyncio.run(_question(finding, question, key, model_name, provider))
    except Exception as exc:
        raise AgentRunError(public_api_error(exc)) from exc
