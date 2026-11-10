import argparse
import csv
import json
from pathlib import Path

from openpyxl import Workbook
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
HEADERS = ["order_id", "invoice_id", "buyer", "seller", "sku", "product",
           "quantity", "unit", "unit_price", "currency", "line_amount",
           "packages", "net_kg", "gross_kg"]


def trio(order, quantity=100, price=20, sku="A-100"):
    common = dict(order_id=order, buyer="Harbor Demo LLC", seller="Pioneer Demo Co.",
                  sku=sku, product="Demo lamp", quantity=str(quantity), unit="PCS")
    return {
        "PO": [{**common, "unit_price": str(price), "currency": "USD"}],
        "CI": [{**common, "invoice_id": f"INV-{order}", "unit_price": str(price),
                "currency": "USD", "line_amount": str(quantity * price)}],
        "PL": [{**common, "packages": "10", "net_kg": "120", "gross_kg": "130"}],
    }


def write_packing_pdf(path, items):
    canvas = Canvas(str(path))
    first = items[0]
    top = 788
    canvas.setFont("Helvetica", 9)
    for field in ("order_id", "invoice_id", "buyer", "seller", "currency"):
        if field in first:
            canvas.drawString(35, top, f"{field}: {first[field]}")
            top -= 18
    columns = ("sku", "product", "quantity", "unit", "packages", "net_kg", "gross_kg")
    canvas.drawString(35, top, " | ".join(columns))
    for item in items:
        top -= 18
        canvas.drawString(35, top, " | ".join(item.get(field, "") for field in columns))
    canvas.save()


def write_case(path, rows, expected, excel=False, pdf=False):
    path.mkdir(parents=True, exist_ok=True)
    for kind, items in rows.items():
        if excel and kind == "PO":
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Order"
            sheet.append(HEADERS)
            for item in items:
                sheet.append([item.get(key) for key in HEADERS])
            workbook.save(path / f"{kind}.xlsx")
        elif pdf and kind == "PL":
            write_packing_pdf(path / "PL.pdf", items)
            (path / "PL.csv").unlink(missing_ok=True)
        else:
            with (path / f"{kind}.csv").open("w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=HEADERS)
                writer.writeheader()
                writer.writerows(items)
    truth = {"findings": expected, "documents": rows}
    (path / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")


def generate_demo():
    normal = trio("PO-001")
    obvious = trio("PO-002")
    obvious["CI"][0].update(quantity="90", unit_price="21", line_amount="1890")
    obvious["PL"][0]["gross_kg"] = "110"
    complex_case = trio("PO-003", 1200)
    other = trio("PO-003", 50, 4, "B-200")
    for kind in complex_case:
        complex_case[kind].extend(other[kind])
    complex_case["PO"][0].update(sku=" a - 100 ", quantity="1 200", unit="件")
    complex_case["CI"].reverse()
    complex_case["CI"].append(dict(complex_case["CI"][0]))
    complex_case["PL"][0].pop("gross_kg")
    for row in complex_case["PL"]:
        row["order_id"] = "PO-999"
    base = ROOT / "data/demo"
    write_case(base / "A", normal, [], excel=True, pdf=True)
    write_case(base / "B", obvious, [
        ["PO_CI_QTY", "difference", "A-100"], ["UNIT_PRICE", "difference", "A-100"],
        ["CI_PL_QTY", "difference", "A-100"], ["WEIGHT", "contradiction", "A-100"],
    ], excel=True)
    write_case(base / "C", complex_case, [
        ["SKU_DUPLICATE", "review", "B-200"], ["ORDER_LINK", "difference", ""],
        ["WEIGHT", "review", "A-100"],
    ], excel=True)


def generate_benchmark():
    for n in range(60):
        sku = f"T-{100 + n}"
        quantity, price = 100 + n, 12 + n % 5
        rows = trio(f"P-{1000 + n}", quantity, price, sku)
        invoice, packing = rows["CI"][0], rows["PL"][0]
        scenario = n % 10
        expected = []
        if scenario == 1:
            invoice.update(quantity=str(quantity - 1), line_amount=str((quantity - 1) * price))
            expected = [["PO_CI_QTY", "difference", sku], ["CI_PL_QTY", "difference", sku]]
        elif scenario == 2:
            invoice.update(unit_price=str(price + 1), line_amount=str(quantity * (price + 1)))
            expected = [["UNIT_PRICE", "difference", sku]]
        elif scenario == 3:
            invoice["line_amount"] = str(quantity * price + 1)
            expected = [["LINE_AMOUNT", "contradiction", sku]]
        elif scenario == 4:
            packing["gross_kg"] = "110"
            expected = [["WEIGHT", "contradiction", sku]]
        elif scenario == 5:
            packing.pop("gross_kg")
            expected = [["WEIGHT", "review", sku]]
        elif scenario == 6:
            invoice["currency"] = "EUR"
            expected = [["CURRENCY", "difference", ""], ["UNIT_PRICE", "review", sku]]
        elif scenario == 7:
            packing["order_id"] = "UNRELATED"
            expected = [["ORDER_LINK", "difference", ""]]
        elif scenario == 8:
            rows["PO"].append(dict(rows["PO"][0]))
            expected = [["SKU_DUPLICATE", "review", sku]]
        elif scenario == 9:
            other = trio(f"P-{1000 + n}", 25, 3, f"ALT-{n}")
            for kind in rows:
                rows[kind].extend(other[kind])
            rows["CI"].reverse()
            rows["PO"][0].update(sku=f" {sku.lower()} ", quantity=f"{quantity},00", unit="件")
        write_case(ROOT / "data/benchmark" / f"{n + 1:03d}", rows, expected,
                   excel=n % 2 == 0, pdf=n % 3 == 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    generate_demo()
    if args.benchmark:
        generate_benchmark()
