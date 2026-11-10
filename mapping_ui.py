import pandas as pd
import streamlit as st

from agent.main_agent import AgentRunError
from agent.mapping_agent import known_mapping, mapping_prompt, suggest_mapping
from agent.tools import confirm_table_mapping
from core.parser import ParseError, inspect_tables
from core.reporter import FIELD_NAMES


def table_label(preview, filename):
    return f"{preview.anchor.source(filename).display()} · " + " / ".join(
        label[:30] for label in preview.labels[:4] if label
    )


def mapping_panel(run, revision, provider, key, model, types):
    with st.expander("确认陌生表头或无边框 PDF", expanded=run.mode == "mapping"):
        st.caption("对照原始列与样例，确认哪些列对应 SKU、数量或重量。确认后重新核验，原文件保留。")
        kinds = [doc.kind for doc in run.documents]
        needs_review = {e.kind for f in run.result.findings if f.category == "review" for e in f.evidence}
        initial = next((i for i, doc in enumerate(run.documents) if not doc.items),
                       next((i for i, kind in enumerate(kinds) if kind in needs_review), 0))
        kind = st.selectbox("选择要检查的单证", kinds, index=initial, format_func=types.get,
                            key=f"map-doc-{revision}")
        file = next(file for file in run.files if file.kind == kind)
        scan_id = (revision, kind)
        if st.button("查看原始表头", key=f"map-scan-{revision}"):
            try:
                previews = inspect_tables(file.name, file.data)
                st.session_state["mapping_scan"] = (scan_id, previews, None)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ParseError) else type(exc).__name__
                st.session_state["mapping_scan"] = (scan_id, [], reason)
        scan = st.session_state.get("mapping_scan")
        if not scan or scan[0] != scan_id:
            return None
        _, previews, error = scan
        if error:
            st.error(error)
            return None
        if not previews:
            st.info("未找到可确认的候选表格。请检查原文件，必要时转成 XLSX 或 CSV。")
            return None
        index = st.selectbox("确认哪一行是表头", range(len(previews)),
                             format_func=lambda i: table_label(previews[i], file.name),
                             key=f"map-table-{revision}-{kind}")
        preview = previews[index]
        table_id = f"{revision}-{kind}-{index}"
        if preview.pdf_rows is not None:
            st.caption(f"本页找到 {preview.pdf_rows} 个连续对齐的候选商品行。请与原 PDF 核对完整性。")
        recognized = known_mapping(preview)
        columns = {i: label for i, label in enumerate(preview.labels, 1) if label}
        rows = []
        for column, label in columns.items():
            samples = [f"第 {row} 行 {values[column - 1]}" for row, values in preview.samples
                       if column <= len(values) and values[column - 1]]
            rows.append({"原列": column, "表头": label, "样例与位置": " / ".join(samples[:2]),
                         "规则识别": FIELD_NAMES.get(recognized.get(column), "待确认")})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

        ai_key = f"mapping_suggestions_{table_id}"
        if mapping_prompt(kind, preview)["unknown"]:
            consent = st.checkbox("允许发送当前候选表头和样例类型摘要给模型",
                                  key=f"map-consent-{table_id}")
            st.caption("样例原值和文件内容留在应用服务器。请先确认表头中没有敏感信息。")
            if st.button("获取 AI 字段建议", disabled=not key or not consent, key=f"map-ai-{table_id}"):
                try:
                    with st.spinner("正在识别表头语义"):
                        suggestions = suggest_mapping(kind, preview, key, model, provider)
                    generation = st.session_state.get(ai_key, (0, {}))[0]
                    st.session_state[ai_key] = (generation + 1, suggestions)
                    st.success(f"模型给出 {len(suggestions)} 个候选映射，等待你确认。")
                except AgentRunError as exc:
                    st.error(f"建议获取失败。仍可手动确认。{exc}")
            if not key:
                st.caption("模型凭据未配置，可手动确认。")
        generation, suggestions = st.session_state.get(ai_key, (0, {}))
        show_known = st.checkbox("同时调整已识别列", key=f"map-known-{table_id}")
        options = [None, *FIELD_NAMES]
        assigned = {} if show_known else dict(recognized)
        with st.form(f"map-confirm-{table_id}-{generation}-{show_known}"):
            layout = st.columns(3)
            for position, (column, label) in enumerate(
                (pair for pair in columns.items() if show_known or pair[0] not in recognized)
            ):
                default = recognized.get(column, suggestions.get(column))
                status = "（AI 建议）" if column in suggestions else ""
                selected = layout[position % 3].selectbox(
                    f"第 {column} 列 · {label[:70]}{status}", options, index=options.index(default),
                    format_func=lambda field: FIELD_NAMES[field] if field else "忽略此列",
                )
                if selected:
                    assigned[column] = selected
            if st.form_submit_button("确认表头并重新审核", type="primary"):
                try:
                    return confirm_table_mapping(run, kind, preview.anchor, assigned)
                except (ParseError, ValueError) as exc:
                    st.error(str(exc))
    return None
