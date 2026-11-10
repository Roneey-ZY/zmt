import io
import json

import pytest
from openpyxl import load_workbook

from agent.mapping_agent import Proposal, ProposalList, known_mapping, mapping_prompt, validate_proposals
from agent.tools import ReviewRun, confirm_table_mapping, revise_preview, run_rules
from core.demo import DATA, load_case
from core.parser import ParseError, TableAnchor, TablePreview, inspect_tables, parse_document
from scripts.evaluate_template_mapping import compare

BASE = DATA / "template_validation"


@pytest.mark.parametrize("folder", ["regression", "new-layouts"])
def test_frozen_fixtures_recover_after_confirmation(folder):
    paths = sorted(path.parent for path in (BASE / folder).glob("*/truth.json"))
    result = compare(paths, live=None)
    assert len(paths) == 5
    assert result["baseline"]["summary"]["anomaly"]["fn"] >= 4
    assert result["confirmed"]["summary"]["anomaly"]["tp"] == 5
    assert all(record["success"] for record in result["confirmed"]["records"])


def test_source_and_confirmation_log_in_excel():
    case = BASE / "regression/03-unfamiliar-invoice-columns"
    files = load_case(case)
    baseline = ReviewRun(files, "zh", "rules")
    run_rules(baseline)
    action = json.loads((case / "confirmation.json").read_text(encoding="utf-8"))[0]
    invoice = next(file for file in files if file.kind == "CI")
    preview = inspect_tables(invoice.name, invoice.data)[0]
    revised = confirm_table_mapping(
        baseline, "CI", preview.anchor,
        known_mapping(preview) | {int(i): value for i, value in action["extra"].items()},
    )
    qty = next(doc for doc in revised.documents if doc.kind == "CI").items[0].fields["quantity"]
    assert qty.raw == "8" and qty.source.row == 2 and qty.source.column == 6
    assert baseline.result.counts["difference"] == 0
    assert revised.result.counts["difference"] == 2
    assert revised.log[0]["tool"] == "apply_confirmed_table"
    report = load_workbook(io.BytesIO(revised.report), data_only=True)
    assert any(row[2:5] == (6, "Billed Units", "数量")
               for row in report["表头确认记录"].values)


def test_api_receives_shapes_and_validates_ambiguous_suggestions():
    preview = TablePreview(TableAnchor(1), ("Qty", "Stock Ref", "Rate Each", "Extended", "Memo"),
                           ((2, ("115", "SECRET-123", "3.40", "391.00", "buyer@example.com")),))
    payload = json.dumps(mapping_prompt("CI", preview))
    for raw in ("SECRET-123", "3.40", "391.00", "buyer@example.com"):
        assert raw not in payload
    reply = ProposalList(proposals=[
        Proposal(column=1, field="unit"),
        Proposal(column=2, field="sku"),
        Proposal(column=3, field="unit_price"),
        Proposal(column=4, field="unit_price"),
        Proposal(column=5, field="quantity"),
        Proposal(column=99, field="net_kg"),
    ])
    assert validate_proposals(preview, reply) == {2: "sku"}


@pytest.mark.parametrize("anchor,fields", [
    (TableAnchor(7), {1: "sku", 2: "quantity"}),
    (TableAnchor(1), {1: "sku", 2: "quantity", 3: "quantity"}),
    (TableAnchor(1), {1: "sku", 9: "quantity"}),
    (TableAnchor(1), {1: "sku", 2: "fabricated_field"}),
])
def test_invalid_confirmation_is_rejected(anchor, fields):
    with pytest.raises(ParseError):
        parse_document("CI", "invoice.csv", b"Ref,Count,Extra\nSKU-1,20,0\n", {anchor: fields})


def test_mapping_preserves_edits_to_other_document():
    case = BASE / "regression/03-unfamiliar-invoice-columns"
    initial = ReviewRun(load_case(case), "zh", "rules")
    run_rules(initial)
    changed = revise_preview(initial, "PL", 0, {"gross_kg": "17"})
    invoice = next(file for file in initial.files if file.kind == "CI")
    table = inspect_tables(invoice.name, invoice.data)[0]
    mapped = confirm_table_mapping(changed, "CI", table.anchor,
                                   known_mapping(table) | {4: "sku", 6: "quantity"})
    weight = next(doc for doc in mapped.documents if doc.kind == "PL").items[0].fields["gross_kg"]
    assert weight.raw == "17" and weight.edited
