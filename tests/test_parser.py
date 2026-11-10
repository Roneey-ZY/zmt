import io

import pytest

from core.parser import ParseError, parse_document


def make_pdf(text=True):
    from reportlab.pdfgen.canvas import Canvas

    output = io.BytesIO()
    canvas = Canvas(output)
    if text:
        for index, line in enumerate((
            "PO Number: PO-123", "Currency: USD", "SKU | Description | Qty | Unit | Unit Price",
            "A-100 | Demo lamp | 12 | PCS | 20",
        )):
            canvas.drawString(45, 770 - 24 * index, line)
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def test_pdf_keeps_page_evidence():
    doc = parse_document("PO", "order.pdf", make_pdf())
    assert doc.header["order_id"].raw == "PO-123"
    assert doc.items[0].fields["quantity"].raw == "12"
    assert doc.items[0].fields["quantity"].source.page == 1


def test_pdf_without_text_is_rejected():
    with pytest.raises(ParseError, match="OCR"):
        parse_document("PO", "blank.pdf", make_pdf(False))


def test_csv_multiline_physical_row():
    raw = 'SKU,Description,Qty,Unit\nA,"Line 1\nLine 2",5,PCS\nB,Other,3,PCS\n'
    doc = parse_document("PO", "order.csv", raw.encode())
    assert [item.anchor.row for item in doc.items] == [2, 4]
