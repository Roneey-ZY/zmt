from dataclasses import dataclass, field
from decimal import Decimal
from time import perf_counter
from typing import Callable

from core.audit_rules import audit
from core.normalizer import CURRENCY_SCALE, normalize_documents, normalize_value
from core.parser import ParseError, parse_document
from core.reporter import describe, make_report
from core.schemas import AuditResult, Document, FieldValue, Finding, Kind, Language, Source

MODES = {
    "rules": ("规则演示模式", "Rules demo"),
    "ai": ("AI 智能体模式", "AI agent"),
    "revision": ("预览修正 · 规则复审", "Preview revision · Rules"),
}


@dataclass(frozen=True)
class InputFile:
    kind: Kind
    name: str
    data: bytes


@dataclass
class ReviewRun:
    files: list[InputFile]
    language: Language
    mode: str
    documents: list[Document] | None = None
    normalized: dict | None = None
    result: AuditResult | None = None
    report: bytes | None = None
    log: list[dict] = field(default_factory=list)
    progress: Callable[[dict], None] | None = None

    def execute(self, operation):
        start = perf_counter()
        event = {"order": len(self.log) + 1, "tool": operation.__name__,
                 "status": "running", "ms": 0, "summary": ""}
        self.log.append(event)
        if self.progress:
            self.progress(event.copy())
        try:
            summary = operation(self)
        except Exception as exc:
            event.update(status="error", summary=str(exc)[:300])
            raise
        else:
            event.update(status="ok", summary=summary)
            return summary
        finally:
            event["ms"] = round((perf_counter() - start) * 1000, 2)
            if self.progress:
                self.progress(event.copy())


def extract_document_fields(run: ReviewRun) -> str:
    documents = []
    for file in run.files:
        try:
            documents.append(parse_document(file.kind, file.name, file.data))
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ParseError) else type(exc).__name__
            raise ParseError(f"{file.name}：{reason}") from exc
    run.documents = documents
    run.normalized = run.result = run.report = None
    return f"{len(documents)} documents, {sum(len(d.items) for d in documents)} item rows"


def normalize_trade_data(run: ReviewRun) -> str:
    if run.documents is None:
        raise ValueError("Call extract_document_fields first.")
    run.normalized = normalize_documents(run.documents)
    run.result = run.report = None
    return f"{len(run.normalized)} distinct field values normalized"


def audit_document_consistency(run: ReviewRun) -> str:
    if run.documents is None or run.normalized is None:
        raise ValueError("Call normalize_trade_data first.")
    run.result = audit(run.documents, run.normalized)
    run.report = None
    return f"{run.result.compared} comparisons, {run.result.counts}"


def export_audit_report(run: ReviewRun) -> str:
    if run.documents is None or run.result is None:
        raise ValueError("Call audit_document_consistency first.")
    run.report = make_report(run.documents, run.result, run.language, MODES[run.mode][run.language == "en"])
    return f"XLSX generated ({len(run.report)} bytes)"


def run_rules(run: ReviewRun) -> None:
    if run.documents is None:
        run.execute(extract_document_fields)
    run.execute(normalize_trade_data)
    run.execute(audit_document_consistency)
    run.execute(export_audit_report)


def revise_preview(run: ReviewRun, kind: Kind, line: int | None,
                   edits: dict[str, str]) -> ReviewRun | None:
    documents = [item.model_copy(deep=True) for item in run.documents]
    target = next(item for item in documents if item.kind == kind)
    fields = target.header if line is None else target.items[line].fields
    changed = False
    for name, raw in edits.items():
        original = fields.get(name)
        if raw == (original.raw if original else ""):
            continue
        source = original.source if original else (
            Source(file=target.file) if line is None else target.items[line].anchor
        )
        fields[name] = FieldValue(raw=raw, source=source, edited=True)
        if line is None:
            target.conflicts.pop(name, None)
        changed = True
    if not changed:
        return None
    revision = ReviewRun(run.files, run.language, "revision", documents=documents)
    run_rules(revision)
    return revision


def model_evidence(finding: Finding, lang: Language) -> dict:
    title, explanation, advice = describe(finding, lang)
    fields = []
    for item in finding.evidence[:8]:
        key = item.field
        raw = item.value
        # Identifiers, company names, filenames and free-form text stay on the server.
        value = None
        if key in {"quantity", "line_amount", "invoice_total", "unit_price", "net_kg",
                   "gross_kg", "currency", "unit"} and raw:
            candidate = normalize_value(key, FieldValue(raw=raw, source=item.source))
            if isinstance(candidate, Decimal) or candidate in CURRENCY_SCALE or candidate in {"PCS", "SET", "KG"}:
                value = str(candidate)
        fields.append({"type": item.kind, "field": key, "value": value, "present": raw is not None})
    if finding.rule == "PARSE_WARNING":
        explanation = title
    return dict(id=finding.id, rule=finding.rule, title=title, category=finding.category,
                explanation=explanation, suggested_check=advice, evidence=fields)
