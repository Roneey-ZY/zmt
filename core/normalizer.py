import re
import unicodedata
from decimal import Decimal

from core.schemas import Document, FieldValue

NUMBER_FIELDS = {"quantity", "unit_price", "line_amount", "invoice_total",
                 "packages", "net_kg", "gross_kg"}
CURRENCY_SCALE = {"USD": 2, "EUR": 2, "CNY": 2, "GBP": 2, "HKD": 2,
                  "AUD": 2, "CAD": 2, "SGD": 2, "CHF": 2, "JPY": 0, "KRW": 0,
                  "KWD": 3, "BHD": 3, "OMR": 3}
UNIT_ALIASES = {
    "PC": "PCS", "PIECE": "PCS", "PIECES": "PCS", "件": "PCS",
    "千克": "KG", "公斤": "KG", "公斤(KG)": "KG",
    "SET": "SET", "SETS": "SET", "套": "SET",
}


def number(text: str) -> Decimal | None:
    # Both separators identify locale. A lone comma with three trailing digits is ambiguous.
    if re.fullmatch(r"[+-]?\d{1,3}( \d{3})+([.,]\d+)?", text):
        text = text.replace(" ", "")
        text = text.replace(",", ".")
    elif re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+\.\d+", text):
        text = text.replace(",", "")
    elif re.fullmatch(r"[+-]?\d{1,3}(\.\d{3})+,\d+", text):
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"[+-]?\d{1,3}(,\d{3}){2,}", text):
        text = text.replace(",", "")
    elif re.fullmatch(r"[+-]?\d+,\d{1,2}", text):
        text = text.replace(",", ".")
    if not re.fullmatch(r"[+-]?(?:\d+|\d*\.\d+)(?:[Ee][+-]?\d{1,2})?", text):
        return None
    value = Decimal(text)
    if not value.is_finite() or abs(value) > Decimal("1e15"):
        return None
    if len(value.as_tuple().digits) > 22 or value.as_tuple().exponent < -10:
        return None
    return value


def normalize_value(field: str, value: FieldValue | None) -> Decimal | str | None:
    if value is None:
        return None
    text = " ".join(unicodedata.normalize("NFKC", value.raw).upper().split())
    if not text or text in {"-", "—", "N/A", "NULL", "NONE", "未知"}:
        return None
    if field in NUMBER_FIELDS:
        if field in {"net_kg", "gross_kg"}:
            text = re.sub(r"\s*(?:KG|千克|公斤)$", "", text)
        return number(text)
    if field == "unit":
        return UNIT_ALIASES.get(text, text)
    if field in {"sku", "order_id", "invoice_id"}:
        return re.sub(r"\s*-\s*", "-", text)
    if field == "currency":
        return {"RMB": "CNY", "US$": "USD", "美元": "USD", "人民币": "CNY"}.get(text, text)
    return text


def normalize_documents(documents: list[Document]) -> dict[tuple[str, str], Decimal | str | None]:
    normalized = {}
    for document in documents:
        for fields in (document.header, *(item.fields for item in document.items)):
            for field, value in fields.items():
                key = field, value.raw
                if key not in normalized:
                    normalized[key] = normalize_value(field, value)
    return normalized
