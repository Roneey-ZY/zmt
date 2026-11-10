import json
from collections import Counter
from pathlib import Path

import pytest

from core.audit_rules import audit
from core.normalizer import number
from core.parser import parse_document

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", ["A", "B", "C"])
def test_demos(case):
    folder = ROOT / "data/demo" / case
    docs = [parse_document(path.stem, path.name, path.read_bytes())
            for path in sorted(folder.iterdir()) if path.suffix in {".xlsx", ".csv", ".pdf"}]
    expected = json.loads((folder / "truth.json").read_text(encoding="utf-8"))["findings"]
    actual = audit(docs)
    assert Counter((f.rule, f.category, f.sku) for f in actual.findings) == Counter(map(tuple, expected))
    assert actual.compared > 0
    assert all(e.source.file for f in actual.findings for e in f.evidence)


@pytest.mark.parametrize("raw,expected", [
    ("1,200.50", "1200.50"), ("1.200,50", "1200.50"), ("1 200", "1200"),
    ("1,200", None), ("NaN", None), ("1e99", None),
])
def test_decimal(raw, expected):
    value = number(raw)
    assert (str(value) if value is not None else None) == expected
