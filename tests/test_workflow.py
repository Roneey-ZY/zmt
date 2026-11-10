import asyncio
import io
import json
from threading import get_ident

import pytest
from openpyxl import load_workbook

from agent.main_agent import AgentRunError, make_agent, run_agent
from agent.tools import ReviewRun, revise_preview, run_rules
from core.demo import DATA, load_case


@pytest.mark.parametrize("lang,sheet", [("zh", "证据"), ("en", "Evidence")])
def test_excel_and_log(lang, sheet):
    run = ReviewRun(load_case(DATA / "demo/B"), lang, "rules")
    run_rules(run)
    wb = load_workbook(io.BytesIO(run.report), data_only=True)
    assert wb[sheet].max_row > 4
    assert run.result.counts == {"difference": 3, "contradiction": 1, "review": 0}
    assert [event["order"] for event in run.log] == [1, 2, 3, 4]
    assert {event["status"] for event in run.log} == {"ok"}


def test_missing_key_does_not_fabricate_tools():
    run = ReviewRun(load_case(DATA / "demo/B"), "zh", "ai")
    with pytest.raises(AgentRunError, match="OPENAI_API_KEY"):
        run_agent(run, "")
    assert run.log == []
    assert run.report is None


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_sdk_runner_with_test_model(provider):
    from agents import Model, RunConfig, Runner
    from agents.items import ModelResponse
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    names = ("extract_document_fields", "normalize_trade_data",
             "audit_document_consistency", "export_audit_report")

    class ScriptedTestModel(Model):
        turn = 0

        async def get_response(self, system_instructions, input, model_settings, tools, *args, **kwargs):
            if provider == "deepseek":
                assert [tool.name for tool in tools] == list(names[self.turn:self.turn + 1])
            self.turn += 1
            if self.turn <= len(names):
                item = ResponseFunctionToolCall(
                    name=names[self.turn - 1], arguments="{}", call_id=f"call_{self.turn}",
                    type="function_call",
                )
            else:
                item = ResponseOutputMessage(
                    id="msg_test", status="completed", role="assistant", type="message",
                    content=[ResponseOutputText(type="output_text", annotations=[], text=json.dumps(
                        {"summary": "Integration fixture", "explanations": []}
                    ))],
                )
            return ModelResponse(output=[item], usage=Usage(), response_id=None)

        def stream_response(self, *args, **kwargs):
            raise AssertionError("Non-streaming integration test")

    progress_threads = []
    run = ReviewRun(load_case(DATA / "demo/B"), "zh", "ai",
                    progress=lambda _: progress_threads.append(get_ident()))
    result = asyncio.run(Runner.run(make_agent(ScriptedTestModel(), provider), "Audit test documents",
                                    context=run, run_config=RunConfig(tracing_disabled=True)))
    assert result.final_output.summary == "Integration fixture"
    assert [entry["tool"] for entry in run.log] == list(names)
    assert all(entry["status"] == "ok" for entry in run.log)
    assert progress_threads == [get_ident()] * 8
    assert run.report


def test_formula_like_input_is_text_in_report():
    from agent.tools import InputFile

    files = load_case(DATA / "demo/B")
    files = [InputFile(f.kind, f.name, f.data.replace(b"Demo lamp", b"=2+3")) for f in files]
    run = ReviewRun(files, "en", "rules")
    run_rules(run)
    wb = load_workbook(io.BytesIO(run.report))
    cells = [row[2] for row in wb["Fields"].iter_rows() if row[2].value == "=2+3"]
    assert len(cells) == 2
    assert all(cell.data_type == "s" for cell in cells)


def test_preview_revision_keeps_original_and_evidence():
    run = ReviewRun(load_case(DATA / "demo/B"), "zh", "rules")
    run_rules(run)
    packing = next(doc for doc in run.documents if doc.kind == "PL")
    original = packing.items[0].fields["gross_kg"]
    revised = revise_preview(run, "PL", 0, {"gross_kg": "130"})
    updated = next(doc for doc in revised.documents if doc.kind == "PL").items[0].fields["gross_kg"]
    assert original.raw == "110" and not original.edited
    assert updated.raw == "130" and updated.edited and updated.source == original.source
    assert revised.files == run.files
    assert [f.rule for f in revised.result.findings] == ["PO_CI_QTY", "UNIT_PRICE", "CI_PL_QTY"]
