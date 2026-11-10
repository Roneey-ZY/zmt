from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["PO", "CI", "PL"]
Language = Literal["zh", "en"]

HEADER_FIELDS = ("order_id", "invoice_id", "buyer", "seller", "currency", "invoice_total")
ITEM_FIELDS = (
    "sku", "product", "quantity", "unit", "unit_price", "line_amount",
    "packages", "net_kg", "gross_kg",
)


class Source(BaseModel):
    file: str
    page: int | None = None
    sheet: str | None = None
    row: int | None = None

    def display(self, lang: Language = "zh") -> str:
        suffix = []
        if self.page is not None:
            suffix.append(f"第 {self.page} 页" if lang == "zh" else f"page {self.page}")
        if self.sheet is not None:
            suffix.append(self.sheet)
        if self.row is not None:
            suffix.append(f"第 {self.row} 行" if lang == "zh" else f"row {self.row}")
        return " · ".join([self.file, *suffix])


class FieldValue(BaseModel):
    raw: str
    source: Source
    edited: bool = False


class Item(BaseModel):
    anchor: Source
    fields: dict[str, FieldValue] = Field(default_factory=dict)


class Document(BaseModel):
    kind: Kind
    file: str
    header: dict[str, FieldValue] = Field(default_factory=dict)
    items: list[Item] = Field(default_factory=list)
    conflicts: dict[str, list[FieldValue]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    kind: Kind
    field: str
    value: str | None
    source: Source
    edited: bool = False


class Finding(BaseModel):
    id: str
    rule: str
    sku: str = ""
    category: Literal["difference", "contradiction", "review"]
    severity: Literal["high", "medium", "review"]
    evidence: list[Evidence]
    detail: str = ""


class AuditResult(BaseModel):
    findings: list[Finding]
    compared: int

    @property
    def counts(self) -> dict[str, int]:
        return {key: sum(f.category == key for f in self.findings)
                for key in ("difference", "contradiction", "review")}
