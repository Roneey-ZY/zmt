import os
from collections import Counter
from time import perf_counter

import pandas as pd
import streamlit as st

from agent.main_agent import AgentRunError, ask_finding, configured_model, run_agent
from agent.tools import InputFile, MODES, ReviewRun, revise_preview, run_rules
from core.demo import DATA, load_case
from core.parser import ParseError
from core.reporter import CATEGORIES, FIELD_NAMES, SEVERITY, SCOPE, describe, make_report
from core.schemas import HEADER_FIELDS, ITEM_FIELDS
from mapping_ui import mapping_panel

st.set_page_config(page_title="贸审通 TradeCheck Agent", page_icon="📋", layout="wide")
from auth import require_login

require_login()

st.markdown("""
<style>
.stMainBlockContainer {max-width: 1150px; padding-top: 2rem;}
.tc-hero {background: #132B49; border-left: 5px solid #55B6CE; color: #F4F8FF;
  padding: 2rem clamp(1rem, 4vw, 3rem); border-radius: 8px; margin: 0 0 1.5rem;}
.tc-hero h1 {font-size: clamp(2rem, 5vw, 3.4rem); color: #fff; padding: .3rem 0 .55rem;}
.tc-hero p {max-width: 42rem; margin-bottom: .45rem; color: #D4E2F2;}
.tc-hero .tc-kicker {font: 600 .82rem ui-monospace, monospace; letter-spacing: .14em; color: #90DFED;}
</style>
<section class="tc-hero" aria-label="贸审通">
<p class="tc-kicker">TRADECHECK AGENT / 单证审核工作台</p>
<h1>贸审通 TradeCheck Agent</h1>
<p>把采购订单、商业发票与装箱单放在一起审核。逐条查看字段差异、原始出处和复核建议。</p>
<p>01 准备单证　　02 执行核验　　03 复核与导出</p>
</section>
""", unsafe_allow_html=True)

TYPES = {"PO": "采购订单", "CI": "商业发票", "PL": "装箱单"}
CASES = {"A": "A · 正常单证", "B": "B · 四项典型异常", "C": "C · 缺值、重复与关联失败",
         "D": "D · 陌生发票列名", "E": "E · 无边框装箱单"}
TEMPLATE_CASES = {"D": "03-unfamiliar-invoice-columns", "E": "05-borderless-packing-list"}
STATE = st.session_state


def setting(name, default=""):
    value = os.environ.get(name)
    if value:
        return value
    try:
        return str(st.secrets.get(name, default))
    except FileNotFoundError:
        return default


provider, key, model = configured_model(setting)


def launch(files, mode, lang, progress, focus):
    run = ReviewRun(files=files, language=lang, mode=mode, progress=progress)
    STATE.pop("baseline", None)
    STATE.pop("answer", None)
    STATE.pop("mapping_scan", None)
    STATE["revision"] = STATE.get("revision", 0) + 1
    summary, error = None, None
    start = perf_counter()
    try:
        if mode == "ai":
            summary = run_agent(run, key, model, focus, provider=provider)
        else:
            run_rules(run)
    except (AgentRunError, ParseError, ValueError) as exc:
        error = str(exc)
    finally:
        run.progress = None
    STATE["record"] = (run, summary, error, perf_counter() - start)


def show_findings(result):
    counts = result.counts
    columns = st.columns(4)
    for col, title, value in zip(columns, ("业务差异", "逻辑矛盾", "无法核验", "已执行核验"),
                                 (*counts.values(), result.compared)):
        col.metric(title, value)
    if not result.findings:
        st.success("在已执行的规则范围内，未发现异常或待复核项。")
    page_size = 15
    pages = (len(result.findings) + page_size - 1) // page_size
    page = st.selectbox("异常页码", range(1, pages + 1)) if pages > 1 else 1
    for finding in result.findings[(page - 1) * page_size:page * page_size]:
        title, explanation, advice = describe(finding, "zh")
        caption = f"{finding.id} · {title} · {CATEGORIES[finding.category][0]}"
        if finding.sku:
            caption += f" · {finding.sku}"
        with st.expander(caption):
            st.text(f"严重程度　{SEVERITY[finding.severity][0]}  |  规则 {finding.rule}")
            st.text(explanation)
            st.dataframe([{
                "单证": TYPES[e.kind], "字段": FIELD_NAMES[e.field],
                "值": e.value if e.value is not None else "缺失", "出处": e.source.display(),
                "预览修改": e.edited,
            } for e in finding.evidence], hide_index=True, width="stretch")
            st.text(advice)


def save_revision(run, revised, start):
    STATE.setdefault("baseline", run.result)
    STATE["record"] = (revised, None, None, perf_counter() - start)
    STATE["revision"] += 1
    STATE.pop("answer", None)


def fingerprint(finding):
    return (finding.rule, finding.category, finding.sku,
            tuple((e.kind, e.field, e.source.display()) for e in finding.evidence))


st.header("01 / 准备单证")
input_mode = st.radio("单证来源", ["演示案例", "上传文件"], horizontal=True)
files = []
if input_mode == "演示案例":
    case = st.selectbox("选择演示案例", CASES, index=1, format_func=CASES.get)
    case_path = (DATA / "template_validation" / "regression" / TEMPLATE_CASES[case]
                 if case in TEMPLATE_CASES else DATA / "demo" / case)
    files = load_case(case_path)
    st.caption("内置单证均为合成数据。加载后立即执行所选模式。")
    if case in TEMPLATE_CASES:
        st.caption("首轮审核后，打开「确认陌生表头或无边框 PDF」查看原始列和复审变化。")
    demo_button = st.container()
else:
    for col, (kind, label) in zip(st.columns(3), TYPES.items()):
        uploaded = col.file_uploader(label, ["pdf", "xlsx", "csv"], key=f"upload_{kind}")
        if uploaded:
            files.append(InputFile(kind, uploaded.name, uploaded.getvalue()))
    st.caption("每类上传一份文件，每份最多 8 MiB。PDF 需包含可选中的文字。")

st.header("02 / 执行审核")
st.caption("AI 模式向模型发送有限的核验字段与填写的指令。请勿在指令里填写敏感信息。")
left, right = st.columns(2)
choice = left.radio("执行模式", ["rules", "ai"], index=int(bool(key)),
                    format_func=lambda mode: MODES[mode][0], horizontal=True)
lang = right.radio("报告语言", ["zh", "en"], format_func=lambda s: "中文" if s == "zh" else "English",
                   horizontal=True)
if not key:
    st.caption("服务器尚未配置模型凭据。规则演示可直接使用，评委无需提供 Key。")
elif choice == "ai":
    st.caption(f"当前模型　{model}")
focus = st.text_input("审核关注点（可选）", max_chars=350,
                      placeholder="例如优先解释发票金额与数量") if choice == "ai" else ""
if input_mode == "演示案例":
    start_audit = demo_button.button("一键加载演示案例", type="primary")
else:
    start_audit = st.button("开始审核", type="primary", disabled=len(files) != 3)
progress_box = st.empty()


def progress(event):
    progress_box.text(f"{event['order']:02d} · {event['tool']} · {event['status']} · {event['ms']} ms")


if start_audit:
    STATE.pop("failed_ai_log", None)
    launch(files, choice, lang, progress, focus)
    progress_box.empty()

record = STATE.get("record")
if record:
    run, summary, error, elapsed = record
    st.caption(f"最近一次审核　{MODES[run.mode][0]} · {elapsed:.2f} 秒")
    if error:
        st.error(error)
        if run.mode == "ai" and st.button("对同一组单证运行规则演示"):
            STATE["failed_ai_log"] = list(run.log)
            launch(run.files, "rules", run.language, progress, "")
            st.rerun()
    if run.log:
        with st.expander("实际执行日志", expanded=run.mode == "ai"):
            st.dataframe(run.log, hide_index=True, width="stretch")
    if STATE.get("failed_ai_log") and run.mode != "ai":
        with st.expander("此前失败的 AI 工具记录"):
            st.dataframe(STATE["failed_ai_log"], hide_index=True, width="stretch")
    if run.documents:
        st.dataframe([{"单证": TYPES[d.kind], "文件": d.file, "商品行": len(d.items),
                       "解析状态": "有待复核项" if d.warnings or d.conflicts else "已提取"}
                      for d in run.documents], hide_index=True, width="stretch")

    if run.result is not None and error is None:
        st.header("03 / 核验结果")
        if run.mode == "mapping" and "baseline" in STATE:
            initial = STATE["baseline"]
            st.success(f"确认后已执行核验 {initial.compared} → {run.result.compared} 项，"
                       f"待复核 {initial.counts['review']} → {run.result.counts['review']} 项。")
            original = Counter(fingerprint(f) for f in initial.findings)
            added = Counter(fingerprint(f) for f in run.result.findings) - original
            new_issues = [describe(f, "zh")[0] for f in run.result.findings
                          if f.category != "review" and fingerprint(f) in added]
            if new_issues:
                st.text("复审新增发现　" + " / ".join(new_issues))
        started = perf_counter()
        mapped = mapping_panel(run, STATE["revision"], provider, key, model, TYPES)
        if mapped is not None:
            save_revision(run, mapped, started)
            st.rerun()
        show_findings(run.result)
        if summary is not None:
            with st.expander("模型解读 · 对照上述规则证据"):
                st.text(summary.summary)
                for entry in summary.explanations:
                    st.text(f"{entry.finding_id}　{entry.explanation}")

        tabs = st.tabs(["复核与修正", "结果问答", "报告导出"])
        with tabs[0]:
            st.caption("修改只作用于当前结构化预览。每次复审保留首轮结果作为对照。")
            kind = st.selectbox("选择待复核单证", list(TYPES), format_func=TYPES.get)
            doc = next(d for d in run.documents if d.kind == kind)
            lines = [None, *range(len(doc.items))]
            line = st.selectbox("字段范围", lines, format_func=lambda index: (
                "单证表头" if index is None else f"商品行 {index + 1} · {doc.items[index].anchor.display()}"
            ))
            fields = doc.header if line is None else doc.items[line].fields
            mapping = [(field, fields.get(field))
                       for field in (HEADER_FIELDS if line is None else ITEM_FIELDS)]
            preview = pd.DataFrame([{
                "字段": FIELD_NAMES[field], "预览值": original.raw if original else "",
                "来源": original.source.display() if original else "待补录",
                "已修改": bool(original and original.edited),
            } for field, original in mapping])
            with st.form(f"revision-{STATE['revision']}-{kind}-{line}"):
                edited = st.data_editor(preview, disabled=["字段", "来源", "已修改"],
                                        hide_index=True, width="stretch")
                if st.form_submit_button("应用预览修改并复审"):
                    start = perf_counter()
                    changes = {field: "" if raw is None else str(raw).strip()
                               for (field, _), raw in zip(mapping, edited["预览值"])}
                    revised = revise_preview(run, kind, line, changes)
                    if revised is not None:
                        save_revision(run, revised, start)
                        st.rerun()
                    st.info("预览值未变化。")
            if "baseline" in STATE:
                baseline = STATE["baseline"]
                before = Counter(fingerprint(f) for f in baseline.findings)
                after = Counter(fingerprint(f) for f in run.result.findings)
                st.text(f"首轮 {len(baseline.findings)} 项 → 当前 {len(run.result.findings)} 项"
                        f"　|　减少 {sum((before - after).values())} 项　新增 {sum((after - before).values())} 项")
                if before - after:
                    st.text("减少项　" + " / ".join(
                        describe(f, "zh")[0] + (f" · {f.sku}" if f.sku else "")
                        for f in baseline.findings if fingerprint(f) in before - after
                    ))
        with tabs[1]:
            if run.result.findings:
                by_id = {f.id: f for f in run.result.findings}
                selected = st.selectbox("选择要了解的异常", list(by_id),
                                        format_func=lambda id_: f"{id_} · {describe(by_id[id_], 'zh')[0]}")
                finding = by_id[selected]
                _, explanation, advice = describe(finding, "zh")
                st.text(f"规则解释　{explanation}")
                st.text(f"检查步骤　{advice}")
                question = st.text_input("追问模型（可选）", max_chars=350, disabled=not bool(key))
                if st.button("发送追问", disabled=not key or not question.strip()):
                    with st.spinner("等待模型回复"):
                        try:
                            answer = ask_finding(finding, question, key, model, provider=provider)
                            STATE["answer"] = (selected, question, answer)
                        except AgentRunError as exc:
                            STATE.pop("answer", None)
                            st.error(str(exc))
                response = STATE.get("answer")
                if response and response[:2] == (selected, question):
                    st.text(response[2])
            else:
                st.info("当前没有可追问的异常或待复核项。")
        with tabs[2]:
            st.caption(SCOPE[lang == "en"])
            report = run.report if lang == run.language else make_report(
                run.documents, run.result, lang, MODES[run.mode][lang == "en"]
            )
            st.download_button("下载 Excel 审核报告", report, f"TradeCheck-{lang}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with st.expander("技术说明与审核范围"):
    st.markdown("""
- **审核链路**　文件解析 → 字段标准化 → 确定性规则 → Excel 证据报告。
- **AI 智能体模式**　服务端的单一 Agent 使用 OpenAI Agents SDK 发起工具调用。页面记录实际执行事件。
- **规则演示模式**　应用直接执行同一组 Python 工具。数量和金额规则在两种模式下采用相同口径。
- **支持范围**　一次关联一笔采购订单。支持常见中英文字段别名、XLSX、CSV、带表格或分隔文本的 PDF。陌生表头和部分无边框文本 PDF 可在确认后复审。模糊字段和重复 SKU 进入人工复核。
- **数据与边界**　预览数据存于当前会话。系统检查内容一致性。扫描件 OCR、汇率转换和海关法律合规判断超出审核范围。
    """)
