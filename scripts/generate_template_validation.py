"""Create synthetic layouts and independently declared expected outcomes."""

import csv
import json
from pathlib import Path

from openpyxl import Workbook
from reportlab.pdfgen.canvas import Canvas

from scripts.generate_demo_data import trio, write_case


ROOT = Path(__file__).resolve().parents[1] / "data/template_validation"
INVOICE_KEYS = ("order_id", "buyer", "seller", "sku", "product", "quantity", "unit",
                "unit_price", "invoice_id", "currency", "line_amount")


def plan(folder, *actions):
    (folder / "confirmation.json").write_text(
        json.dumps(actions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def borderless(folder, rows, *, separate_pages=False):
    keys = ("sku", "product", "quantity", "unit", "packages", "net_kg", "gross_kg")
    labels = ("Product Code", "Description", "Qty", "UOM", "CTNS",
              "Net Weight (kg)", "Gross Weight (kg)")
    positions = [32, 122, 257, 307, 361, 419, 500]
    path = folder / "PL.pdf"
    pdf = Canvas(str(path), invariant=1)
    pages = [[row] for row in rows] if separate_pages else [rows]
    for group in pages:
        pdf.setFont("Helvetica", 9)
        pdf.drawString(32, 759, "Packing worksheet / Independent synthetic sample")
        pdf.drawString(32, 729, f"Order Number: {group[0]['order_id']}")
        pdf.drawString(32, 711, f"Buyer: {group[0]['buyer']}")
        pdf.drawString(32, 693, f"Seller: {group[0]['seller']}")
        for pos, value in zip(positions, labels):
            pdf.drawString(pos, 635, value)
        for index, row in enumerate(group, 1):
            for pos, key in zip(positions, keys):
                pdf.drawString(pos, 635 - 24 * index, row.get(key, ""))
        pdf.drawString(32, 42, "Synthetic fixture")
        pdf.showPage()
    pdf.save()
    (folder / "PL.csv").unlink(missing_ok=True)
    plan(folder, *[{"kind": "PL", "page": n, "row": 5, "extra": {}}
                   for n in range(1, len(pages) + 1)])


def generate():
    regression = ROOT / "regression"
    plan(regression / "03-unfamiliar-invoice-columns", {
        "kind": "CI", "row": 1, "extra": {4: "sku", 6: "quantity", 8: "unit_price", 9: "line_amount"},
    })
    plan(regression / "05-borderless-packing-list", {
        "kind": "PL", "page": 1, "row": 4, "extra": {},
    })

    base = ROOT / "new-layouts"
    rows = trio("QA-731", 25, 12, "RC-731")
    extra = trio("QA-731", 10, 7, "RC-732")
    for kind in rows:
        rows[kind].extend(extra[kind])
    rows["CI"][0].update(quantity="23", line_amount="276")
    rows["CI"].reverse()
    folder = base / "01-new-invoice-csv"
    write_case(folder, rows, [["PO_CI_QTY", "difference", "RC-731"], ["CI_PL_QTY", "difference", "RC-731"]])
    with (folder / "CI.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["PO No", "Buyer", "Seller", "Article Ref.", "Product", "Units Invoiced",
                         "Unit", "Selling Price per Unit", "Invoice Number", "Currency", "Extended Net"])
        writer.writerows([row[key] for key in INVOICE_KEYS] for row in rows["CI"])
    plan(folder, {"kind": "CI", "row": 1,
                  "extra": {4: "sku", 6: "quantity", 8: "unit_price", 11: "line_amount"}})

    rows = trio("QA-841", 16, 4, "RC-841")
    rows["CI"][0].update(unit_price="5", line_amount="80")
    folder = base / "02-new-invoice-xlsx"
    write_case(folder, rows, [["UNIT_PRICE", "difference", "RC-841"]])
    book = Workbook()
    book.active.title = "说明"
    book.active.append(["合成验证样本"])
    sheet = book.create_sheet("货物结算")
    sheet.append(["货物结算清单"])
    sheet.append([])
    sheet.append(["订单编号", "买方", "卖方", "目录物料代号", "品名", "本次结算件数",
                  "计量单位", "每件成交价", "发票编号", "币种", "本行小计"])
    sheet.append([rows["CI"][0][key] for key in INVOICE_KEYS])
    book.save(folder / "CI.xlsx")
    (folder / "CI.csv").unlink(missing_ok=True)
    plan(folder, {"kind": "CI", "sheet": "货物结算", "row": 3,
                  "extra": {4: "sku", 6: "quantity", 8: "unit_price", 11: "line_amount"}})

    rows = trio("QA-951", 12, 5, "RC-951")
    extra = trio("QA-951", 9, 3, "RC-952")
    for kind in rows:
        rows[kind].extend(extra[kind])
    rows["PL"][0].update(quantity="11")
    rows["PL"][1].update(net_kg="11", gross_kg="8")
    folder = base / "03-two-page-borderless"
    write_case(folder, rows, [["CI_PL_QTY", "difference", "RC-951"], ["WEIGHT", "contradiction", "RC-952"]])
    borderless(folder, rows["PL"], separate_pages=True)

    rows = trio("QA-661", 8, 6, "RC-661")
    rows["PL"][0].pop("gross_kg")
    folder = base / "04-missing-weight-stays-review"
    write_case(folder, rows, [["WEIGHT", "review", "RC-661"]])
    borderless(folder, rows["PL"])

    rows = trio("QA-521", 5, 7, "RC-521")
    folder = base / "05-optional-column-ignored"
    write_case(folder, rows, [])
    with (folder / "CI.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        data = list(csv.reader(stream))
    with (folder / "CI.csv").open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([data[0] + ["Internal handling note"],
                                     data[1] + ["Keep this box upright"]])


if __name__ == "__main__":
    generate()
