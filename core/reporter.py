from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from core.schemas import AuditResult, Document, Finding, Language

SCOPE = (
    "本报告仅核对单证内容一致性，不提供海关法律合规保证。",
    "This report checks document consistency only and provides no customs or legal compliance assurance.",
)
RULES = {
    "ORDER_LINK": ("订单编号关联", "Purchase order link", "核对两份单证引用的采购订单编号。", "Verify both purchase order references."),
    "INVOICE_LINK": ("发票编号关联", "Invoice link", "核对装箱单引用的发票编号。", "Verify the invoice reference on the packing list."),
    "INVOICE_ID": ("发票编号待补", "Invoice number required", "从发票原件补录发票编号。", "Supply the invoice number from the source."),
    "PO_CI_QTY": ("订单与发票数量", "PO vs invoice quantity", "核对订单、实际出货或分批开票数量及单位。", "Check ordered, shipped or partially invoiced quantities and units."),
    "CI_PL_QTY": ("发票与装箱单数量", "Invoice vs packing quantity", "用实际装箱记录核对数量与计量单位。", "Reconcile quantities and units against packing records."),
    "UNIT_PRICE": ("订单与发票单价", "PO vs invoice unit price", "核对议定单价、计价单位和币种。", "Verify the agreed unit price, pricing unit and currency."),
    "LINE_AMOUNT": ("发票行金额", "Invoice line amount", "核对数量乘单价及币种精度，并检查是否存在单列折扣。", "Check quantity × unit price, currency precision and separately stated discounts."),
    "INVOICE_TOTAL": ("发票总额", "Invoice total", "核对行金额合计，另行确认运费、税费和折扣。", "Check the line sum and reconcile freight, taxes and discounts separately."),
    "CURRENCY": ("币种一致性", "Currency consistency", "确认双方约定的币种代码。", "Confirm the agreed currency code."),
    "WEIGHT": ("装箱重量逻辑", "Packing weight logic", "复核称重记录，确认 0 ≤ 净重 ≤ 毛重，单位为 kg。", "Verify weighing records and confirm 0 ≤ net ≤ gross, in kg."),
    "SKU_LINK": ("SKU 关联失败", "SKU link unresolved", "核对商品编码及是否存在漏行或替代品。", "Check item codes and look for missing rows or substitutions."),
    "SKU_DUPLICATE": ("SKU 重复", "Duplicate SKU", "确认是否分批或分规格，必要时补充唯一行标识。", "Check for batches or variants and provide a unique line identifier if needed."),
    "SKU_MISSING": ("SKU 缺失", "SKU missing", "补录原始单证中的商品编码。", "Supply the item code from the source."),
    "HEADER_CONFLICT": ("表头存在多个取值", "Conflicting header values", "核对该文件是否混有多笔交易或存在表头录入错误。", "Check for mixed transactions or a header entry error."),
    "NO_ITEMS": ("未识别商品行", "No recognized item rows", "检查模板和表头，必要时转为受支持的 CSV 模板。", "Check the layout and headers, or export to the supported CSV template."),
    "PARSE_WARNING": ("页面或表格需复核", "Page or table needs review", "对照原文件确认空页或未映射行是否包含待核验信息。", "Check whether blank pages or unmapped rows contain relevant data."),
}
CATEGORIES = {"difference": ("业务差异", "Business difference"),
              "contradiction": ("逻辑矛盾", "Logical contradiction"),
              "review": ("无法核验", "Manual review")}
SEVERITY = {"medium": ("中", "Medium"), "high": ("高", "High"), "review": ("待复核", "Review")}
FIELD_NAMES = dict(zip(
    ("order_id", "invoice_id", "buyer", "seller", "currency", "invoice_total", "sku",
     "product", "quantity", "unit", "unit_price", "line_amount", "packages", "net_kg", "gross_kg"),
    ("订单编号", "发票编号", "买方", "卖方", "币种", "发票总额", "SKU", "商品名称", "数量",
     "计量单位", "单价", "行金额", "包装件数", "净重(kg)", "毛重(kg)"),
))


def describe(finding: Finding, lang: Language):
    index = int(lang == "en")
    title, advice = RULES[finding.rule][index], RULES[finding.rule][index + 2]
    explanation = {
        "review": ("所需字段缺失、不可比较或关联有歧义。", "Required evidence is missing, incomparable or ambiguous."),
        "difference": ("可比较字段的值存在差异。", "Comparable values differ."),
        "contradiction": ("字段值违反确定性审核规则。", "The values violate a deterministic check."),
    }[finding.category][index]
    if finding.rule == "INVOICE_TOTAL" and finding.detail:
        explanation = ("发票总额与行金额合计有差额，需人工确认额外费用或折扣。",
                       "The invoice total differs from the line sum. Check charges and discounts.")[index]
    if finding.detail:
        label = ("规则计算值", "Calculated value")[index] if finding.rule in {
            "LINE_AMOUNT", "INVOICE_TOTAL"} else ("解析记录", "Parser note")[index]
        explanation += f" {label} {finding.detail}"
    return title, explanation, advice


def make_report(documents: list[Document], result: AuditResult, lang: Language, mode: str) -> bytes:
    en = lang == "en"
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary" if en else "审核总览"
    summary.append(["Metric", "Value"] if en else ["指标", "结果"])
    summary.append(["Mode" if en else "模式", mode])
    summary.append(["Completed checks" if en else "已执行核验", result.compared])
    summary.append(["Detected anomalies" if en else "已发现异常",
                    result.counts["difference"] + result.counts["contradiction"]])
    for category, count in result.counts.items():
        summary.append([CATEGORIES[category][en], count])
    summary.append(["Scope" if en else "审核范围", SCOPE[en]])

    files = wb.create_sheet("Files" if en else "单证清单")
    files.append(["Type", "File", "Item rows"] if en else ["类型", "文件", "商品行"])
    for doc in documents:
        files.append([doc.kind, doc.file, len(doc.items)])

    details = wb.create_sheet("Findings" if en else "异常与复核")
    details.append(
        ["ID", "Rule", "Check", "SKU", "Category", "Severity", "Explanation", "Suggested action"]
        if en else ["编号", "规则", "核验项", "SKU", "分类", "严重程度", "说明", "建议"]
    )
    sources = wb.create_sheet("Evidence" if en else "证据")
    sources.append(["ID", "Type", "Field", "Value", "Source", "Edited"]
                   if en else ["编号", "类型", "字段", "值", "来源", "预览修改"])
    for finding in result.findings:
        title, explanation, advice = describe(finding, lang)
        details.append([finding.id, finding.rule, title, finding.sku, CATEGORIES[finding.category][en],
                        SEVERITY[finding.severity][en], explanation, advice])
        for item in finding.evidence:
            sources.append([finding.id, item.kind, item.field if en else FIELD_NAMES[item.field],
                            item.value if item.value is not None else ("Missing" if en else "缺失"),
                            item.source.display(lang), item.edited])

    extracted = wb.create_sheet("Fields" if en else "结构化字段")
    extracted.append(["Type", "Field", "Value", "Source", "Edited"]
                     if en else ["类型", "字段", "值", "来源", "预览修改"])
    for doc in documents:
        for group in (doc.header, *(item.fields for item in doc.items)):
            for field, item in group.items():
                extracted.append([doc.kind, field if en else FIELD_NAMES[field], item.raw,
                                  item.source.display(lang), item.edited])

    if any(doc.confirmed_tables for doc in documents):
        mapping = wb.create_sheet("Confirmed columns" if en else "表头确认记录")
        mapping.append(["Type", "Header source", "Column", "Original header", "Mapped field"] if en
                       else ["单证", "表头出处", "原列号", "原始表头", "确认字段"])
        for doc in documents:
            for table in doc.confirmed_tables:
                for column in table.columns:
                    mapping.append([doc.kind, table.header.display(lang), column.column, column.label,
                                    column.field if en else FIELD_NAMES[column.field]])

    for sheet in wb:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            size = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[get_column_letter(column[0].column)].width = min(65, max(13, size + 4))
            for cell in column:
                if isinstance(cell.value, str):
                    # User-supplied text must remain inert in exported workbooks.
                    cell.data_type = "s"
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="132B49")
            cell.font = Font(color="FFFFFF", bold=True)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
