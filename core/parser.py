import csv
import io
import re
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import openpyxl
import pdfplumber

from core.normalizer import normalize_value
from core.pdf_columns import borderless_tables
from core.schemas import (
    ColumnMapping, ConfirmedTable, Document, FieldValue, HEADER_FIELDS, ITEM_FIELDS,
    Item, Kind, Source,
)

MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 2000
MAX_PAGES = 30

ALIASES = {
    "order_id": "PO Number|Purchase Order No|PO No|Order Number|Order No|订单编号|采购订单号",
    "invoice_id": "Invoice Number|Invoice No|发票编号|发票号",
    "buyer": "Buyer|Bill To|买方|买家",
    "seller": "Seller|Supplier|卖方|供应商",
    "currency": "Currency|CCY|币种|货币",
    "invoice_total": "Invoice Total|Grand Total|发票总金额|总计金额",
    "sku": "SKU|Item Code|Product Code|Part No|商品编码|货号",
    "product": "Product|Product Name|Description|Item Description|商品名称|品名",
    "quantity": "Quantity|Qty|数量",
    "unit": "Unit|UOM|计量单位|单位",
    "unit_price": "Unit Price|单价",
    "line_amount": "Line Amount|Line Total|Amount|金额|行金额",
    "packages": "Packages|Cartons|CTNS|包装件数|箱数",
    "net_kg": "Net Weight (kg)|N.W. (kg)|Net kg|净重(kg)|净重（公斤）",
    "gross_kg": "Gross Weight (kg)|G.W. (kg)|Gross kg|毛重(kg)|毛重（公斤）",
}


def label_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


LABELS = {
    label_key(label): field
    for field, names in ALIASES.items() for label in [field, *names.split("|")]
}


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class TableAnchor:
    row: int
    sheet: str | None = None
    page: int | None = None

    def source(self, filename: str) -> Source:
        return Source(file=filename, sheet=self.sheet, page=self.page, row=self.row)


@dataclass(frozen=True)
class TablePreview:
    anchor: TableAnchor
    labels: tuple[str, ...]
    samples: tuple[tuple[int, tuple[str, ...]], ...]
    pdf_rows: int | None = None


def clean(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()[:1000]


def table_rows(data: bytes, ext: str):
    if ext == ".csv":
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                content = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ParseError("CSV 编码无法识别，请使用 UTF-8 或 GB18030。")
        if "\x00" in content:
            raise ParseError("CSV 包含 NUL 字节，请重新导出为 UTF-8。")
        try:
            dialect = csv.Sniffer().sniff(content[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = "excel"
        reader = csv.reader(io.StringIO(content), dialect)
        while True:
            row = reader.line_num + 1
            values = next(reader, None)
            if values is None:
                break
            yield None, row, values
        return

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 40 * 1024 * 1024:
            raise ParseError("XLSX 解压大小超出处理上限。")
    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    try:
        for sheet in book:
            if sheet.max_column and sheet.max_column > 160:
                raise ParseError("XLSX 列数超出处理上限。")
            for row, values in enumerate(sheet.values, 1):
                yield sheet.title, row, values
    finally:
        book.close()


def checked_format(name: str, data: bytes) -> str:
    if not data or len(data) > MAX_BYTES:
        raise ParseError("文件为空或超过 8 MiB。")
    ext = Path(name).suffix.lower()
    if ext not in {".csv", ".xlsx", ".pdf"}:
        raise ParseError("支持文本型 PDF、XLSX 和 CSV。")
    return ext


def checked_rows(data: bytes, ext: str):
    for count, (sheet, row, values) in enumerate(table_rows(data, ext), 1):
        if count > MAX_ROWS:
            raise ParseError("单证超过 2000 行。")
        if len(values) > 160:
            raise ParseError("表格列数超出 160 列。")
        yield sheet, row, values


def pdf_tables(page):
    return borderless_tables(page, lambda label: LABELS.get(label_key(label)))


def inspect_tables(name: str, data: bytes) -> list[TablePreview]:
    ext = checked_format(name, data)
    if ext == ".pdf":
        previews = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise ParseError("PDF 超过 30 页。")
            for page in pdf.pages:
                if page.find_tables() or "|" in (page.extract_text() or ""):
                    continue
                for line, labels, rows in pdf_tables(page):
                    previews.append(TablePreview(TableAnchor(line, page=page.page_number),
                                                 labels, tuple(rows[:4]), len(rows)))
        return previews

    sheets = defaultdict(list)
    for sheet, row, values in checked_rows(data, ext):
        values = tuple(clean(value) for value in values)
        if any(values):
            sheets[sheet].append((row, values))
    previews = []
    for sheet, rows in sheets.items():
        candidates = []
        for i, (row, labels) in enumerate(rows[:60]):
            text_columns = sum(any(char.isalpha() for char in label) for label in labels)
            samples = tuple(rows[i + 1:i + 5])
            if text_columns < 2 or not samples or len(labels) < 2:
                continue
            known = sum(label_key(label) in LABELS for label in labels)
            candidates.append(((known, text_columns, -row),
                               TablePreview(TableAnchor(row, sheet), labels, samples)))
        previews.extend(preview for _, preview in sorted(candidates, key=lambda pair: pair[0],
                                                          reverse=True)[:8])
    return previews


def parse_document(kind: Kind, name: str, data: bytes,
                   mappings: dict[TableAnchor, dict[int, str]] | None = None) -> Document:
    ext = checked_format(name, data)
    mappings = mappings or {}
    anchors = {(anchor.sheet, anchor.page): anchor for anchor in mappings}
    if len(anchors) != len(mappings):
        raise ParseError("每个工作表或 PDF 页面只允许确认一张商品表。")
    applied = set()
    doc = Document(kind=kind, file=name)
    columns: list[str | None] = []
    confirmed = False

    def confirm(values, anchor):
        nonlocal columns, confirmed
        selected = mappings[anchor]
        if (len(selected) != len(set(selected.values())) or
                any(index < 1 or index > len(values) or not clean(values[index - 1])
                    for index in selected) or
                any(field not in (*HEADER_FIELDS, *ITEM_FIELDS) for field in selected.values()) or
                "sku" not in selected.values() or
                len(set(selected.values()) & set(ITEM_FIELDS)) < 2):
            raise ParseError("确认表头时须唯一映射 SKU 和至少一个商品字段，且列必须来自原表。")
        columns = [selected.get(index) for index in range(1, len(values) + 1)]
        confirmed = True
        applied.add(anchor)
        doc.confirmed_tables.append(ConfirmedTable(header=anchor.source(name), columns=[
            ColumnMapping(column=index, label=clean(values[index - 1]), field=field)
            for index, field in sorted(selected.items())
        ]))

    def header_value(key: str, raw: str, source: Source) -> None:
        value = FieldValue(raw=raw, source=source)
        first = doc.header.setdefault(key, value)
        def canonical(field_value):
            parsed = normalize_value(key, field_value)
            return field_value.raw if parsed is None else parsed

        if canonical(first) != canonical(value):
            values = doc.conflicts.setdefault(key, [first])
            if all(canonical(v) != canonical(value) for v in values):
                values.append(value)

    def consume(values, source: Source) -> None:
        nonlocal columns, confirmed
        values = [clean(value) for value in values]
        labels = [LABELS.get(label_key(value)) for value in values]
        if sum(field in ITEM_FIELDS for field in labels) >= 2 and (
            "sku" in labels or "product" in labels
        ):
            if len([f for f in labels if f]) != len(set(f for f in labels if f)):
                raise ParseError("发现重复映射的列标题，请检查表头。")
            columns = labels
            confirmed = False
            return
        if len(values) == 2 and labels[0] in HEADER_FIELDS and values[1]:
            header_value(labels[0], values[1], source)
            return
        if not columns or not any(values):
            return
        pairs = [(index, key, value) for index, (key, value) in enumerate(zip(columns, values), 1)
                 if key and value]
        def cell_source(index):
            return source.model_copy(update={"column": index}) if confirmed else source

        for index, key, value in pairs:
            if key in HEADER_FIELDS:
                header_value(key, value, cell_source(index))
        fields = {key: FieldValue(raw=value, source=cell_source(index))
                  for index, key, value in pairs if key in ITEM_FIELDS}
        if fields:
            doc.items.append(Item(anchor=source, fields=fields))
        elif any(values) and not pairs:
            doc.warnings.append(f"unmapped:{source.display('en')}")

    if ext in {".csv", ".xlsx"}:
        last_sheet = None
        for sheet, row, values in checked_rows(data, ext):
            if sheet != last_sheet:
                columns = []
                confirmed = False
                last_sheet = sheet
            source = Source(file=name, sheet=sheet, row=row)
            anchor = anchors.get((sheet, None))
            if anchor is not None and row == anchor.row:
                confirm(values, anchor)
            elif anchor is not None and row < anchor.row:
                if len(values) >= 2 and not any(clean(v) for v in values[2:]):
                    key = LABELS.get(label_key(clean(values[0])))
                    if key in HEADER_FIELDS and clean(values[1]):
                        header_value(key, clean(values[1]), source)
            else:
                consume(values, source)
    elif ext == ".pdf":
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise ParseError("PDF 超过 30 页。")
            text_pages = 0
            for page in pdf.pages:
                source = Source(file=name, page=page.page_number)
                content = page.extract_text() or ""
                if not content.strip():
                    doc.warnings.append(f"empty_page:{page.page_number}")
                    continue
                text_pages += 1
                tables = page.find_tables()
                anchor = anchors.get((None, page.page_number))
                if anchor is not None and (tables or "|" in content):
                    raise ParseError("该 PDF 页面已有常规表格，请使用常规解析。")
                if anchor is not None:
                    columns = []
                    confirmed = False
                for line in content.splitlines():
                    parts = re.split(r"[:：]", line, maxsplit=1)
                    if len(parts) == 2 and LABELS.get(label_key(parts[0])) in HEADER_FIELDS:
                        consume(parts, source)
                    elif anchor is None and not tables and "|" in line:
                        consume(line.split("|"), source)
                for table in tables:
                    for row in table.extract():
                        consume(row, source)
                if anchor is not None:
                    for row, labels, rows in pdf_tables(page):
                        if row != anchor.row:
                            continue
                        confirm(labels, anchor)
                        for line, values in rows:
                            consume(values, Source(file=name, page=page.page_number, row=line))
                        columns = []
                        confirmed = False
                        break
                elif mappings and not tables and "|" not in content and next(pdf_tables(page), None):
                    doc.warnings.append(f"unconfirmed_pdf_table:{page.page_number}")
                if len(doc.items) > MAX_ROWS:
                    raise ParseError("单证超过 2000 个商品行。")
            if not text_pages:
                raise ParseError("PDF 无可提取文字。当前版本不支持扫描件或图片 OCR。")
    if applied != mappings.keys():
        raise ParseError("确认的表头已不可用，请重新选择原单证和表头。")

    if not doc.items:
        doc.warnings.append("no_items")
    return doc
