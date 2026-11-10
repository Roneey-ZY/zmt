from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP, localcontext

from core.normalizer import CURRENCY_SCALE, normalize_documents
from core.schemas import AuditResult, Document, Evidence, Finding, Item, Source


def evidence(doc: Document, field: str, item: Item | None = None) -> Evidence:
    fields = item.fields if item else doc.header
    value = fields.get(field)
    return Evidence(
        kind=doc.kind, field=field, value=value.raw if value else None,
        source=value.source if value else item.anchor if item else Source(file=doc.file),
        edited=value.edited if value else False,
    )


def audit(documents: list[Document], normalized=None) -> AuditResult:
    docs = {doc.kind: doc for doc in documents}
    if len(documents) != 3 or set(docs) != {"PO", "CI", "PL"}:
        raise ValueError("每次审核需要一份 PO、一份 CI 和一份 PL。")
    po, ci, pl = (docs[kind] for kind in ("PO", "CI", "PL"))
    findings = []
    compared = 0
    indexes = {}
    if normalized is None:
        normalized = normalize_documents(documents)

    def add(rule, category, fields, sku="", detail=""):
        severity = {"contradiction": "high", "difference": "medium", "review": "review"}
        findings.append(Finding(id=f"F{len(findings) + 1:03d}", rule=rule, sku=sku,
                                category=category, severity=severity[category],
                                evidence=fields, detail=detail))

    def header(doc, field):
        return None if field in doc.conflicts else field_value(doc.header, field)

    def field_value(fields, field):
        raw = fields.get(field)
        return normalized[field, raw.raw] if raw is not None else None

    def value(item, field):
        return field_value(item.fields, field)

    def check(rule, fields, usable, matches, category, sku="", detail=""):
        nonlocal compared
        if not usable:
            add(rule, "review", fields, sku, detail)
            return False
        compared += 1
        if not matches:
            add(rule, category, fields, sku, detail)
        return matches

    for doc in documents:
        if not doc.items:
            add("NO_ITEMS", "review", [evidence(doc, "sku")])
        for warning in doc.warnings:
            if warning != "no_items":
                add("PARSE_WARNING", "review", [evidence(doc, "sku")], detail=warning)
        for field, values in doc.conflicts.items():
            add("HEADER_CONFLICT", "review", [
                Evidence(kind=doc.kind, field=field, value=v.raw, source=v.source) for v in values
            ])
        index = defaultdict(list)
        for item in doc.items:
            sku = value(item, "sku")
            if sku is None:
                add("SKU_MISSING", "review", [evidence(doc, "sku", item)])
            else:
                index[sku].append(item)
        for sku, items in index.items():
            if len(items) > 1:
                add("SKU_DUPLICATE", "review", [evidence(doc, "sku", i) for i in items], sku)
        indexes[doc.kind] = index

    if header(ci, "invoice_id") is None:
        add("INVOICE_ID", "review", [evidence(ci, "invoice_id")])
    ci_invoice, pl_invoice = header(ci, "invoice_id"), header(pl, "invoice_id")
    linked_invoice = True
    if pl_invoice is not None or "invoice_id" in pl.conflicts:
        linked_invoice = check(
            "INVOICE_LINK", [evidence(ci, "invoice_id"), evidence(pl, "invoice_id")],
            ci_invoice is not None and pl_invoice is not None, ci_invoice == pl_invoice,
            "difference",
        )

    def order_link(left, right):
        a, b = header(left, "order_id"), header(right, "order_id")
        return check("ORDER_LINK", [evidence(left, "order_id"), evidence(right, "order_id")],
                     a is not None and b is not None, a == b, "difference")

    po_ci = order_link(po, ci)
    ci_pl = order_link(ci, pl)
    po_ccy, ci_ccy = header(po, "currency"), header(ci, "currency")
    currency_same = False
    if po_ci:
        currency_same = check(
            "CURRENCY", [evidence(po, "currency"), evidence(ci, "currency")],
            po_ccy in CURRENCY_SCALE and ci_ccy in CURRENCY_SCALE, po_ccy == ci_ccy,
            "difference",
        )
    if ci_pl and linked_invoice and ("currency" in pl.header or "currency" in pl.conflicts):
        pl_ccy = header(pl, "currency")
        check("CURRENCY", [evidence(ci, "currency"), evidence(pl, "currency")],
              ci_ccy in CURRENCY_SCALE and pl_ccy in CURRENCY_SCALE, ci_ccy == pl_ccy,
              "difference")

    def paired(left, right, quantity_rule, compare_price=False):
        a, b = indexes[left.kind], indexes[right.kind]
        for sku in sorted(a.keys() | b.keys()):
            left_items, right_items = a.get(sku, []), b.get(sku, [])
            if not left_items or not right_items:
                add("SKU_LINK", "review", [
                    evidence(left, "sku", left_items[0] if left_items else None),
                    evidence(right, "sku", right_items[0] if right_items else None),
                ], sku)
                continue
            if len(left_items) != 1 or len(right_items) != 1:
                continue
            i, j = left_items[0], right_items[0]
            units = [value(i, "unit"), value(j, "unit")]
            same_units = units[0] is not None and units[0] == units[1]
            comparisons = [("quantity", quantity_rule)]
            if compare_price:
                comparisons.append(("unit_price", "UNIT_PRICE"))
            for field, rule in comparisons:
                fields = [evidence(left, field, i), evidence(right, field, j)]
                numbers = [value(i, field), value(j, field)]
                usable = same_units and all(isinstance(v, Decimal) and v >= 0 for v in numbers)
                if not same_units:
                    fields.extend([evidence(left, "unit", i), evidence(right, "unit", j)])
                if field == "unit_price" and not currency_same:
                    usable = False
                    fields.extend([evidence(left, "currency"), evidence(right, "currency")])
                check(rule, fields, usable, numbers[0] == numbers[1], "difference", sku)

    if po_ci:
        paired(po, ci, "PO_CI_QTY", compare_price=True)
    if ci_pl and linked_invoice:
        paired(ci, pl, "CI_PL_QTY")

    scale = CURRENCY_SCALE.get(ci_ccy)
    quantum = Decimal(1).scaleb(-scale) if scale is not None else None
    with localcontext() as context:
        context.prec = 64
        line_amounts = []
        for item in ci.items:
            keys = ("quantity", "unit_price", "line_amount")
            qty, price, actual = (value(item, key) for key in keys)
            fields = [evidence(ci, key, item) for key in keys] + [evidence(ci, "currency")]
            sku = value(item, "sku") or ""
            valid = quantum is not None and all(
                isinstance(v, Decimal) and v >= 0 for v in (qty, price, actual)
            )
            expected = (qty * price).quantize(quantum, rounding=ROUND_HALF_UP) if valid else None
            check("LINE_AMOUNT", fields, valid, actual == expected, "contradiction",
                  sku, str(expected) if expected is not None else "")
            line_amounts.append(actual)

        if "invoice_total" in ci.header:
            total = header(ci, "invoice_total")
            usable = (
                quantum is not None and isinstance(total, Decimal) and total >= 0
                and bool(line_amounts)
                and all(isinstance(amount, Decimal) and amount >= 0 for amount in line_amounts)
            )
            expected = sum(line_amounts, Decimal(0)) if usable else None
            fields = [evidence(ci, "invoice_total"), evidence(ci, "currency")]
            fields.extend(evidence(ci, "line_amount", item) for item in ci.items)
            # Invoice grand totals may include freight, taxes or discounts beyond line amounts.
            check("INVOICE_TOTAL", fields, usable, total == expected, "review",
                  detail=str(expected) if expected is not None else "")

    for item in pl.items:
        net, gross = value(item, "net_kg"), value(item, "gross_kg")
        valid = isinstance(net, Decimal) and isinstance(gross, Decimal)
        check("WEIGHT", [evidence(pl, field, item) for field in ("net_kg", "gross_kg")],
              valid, valid and 0 <= net <= gross, "contradiction", value(item, "sku") or "")

    return AuditResult(findings=findings, compared=compared)
