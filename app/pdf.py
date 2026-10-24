from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .money import fmt_money, fmt_qty


def build_invoice_pdf(sale, business, created_str):
    cur = business.get("currency", "")
    st = getSampleStyleSheet()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Invoice {sale.invoice_no}")
    story = [Paragraph(escape(business.get("name", "")), st["Title"])]
    for line in (business.get("address"), business.get("phone")):
        if line:
            story.append(Paragraph(escape(line), st["Normal"]))
    story += [Spacer(1, 10),
              Paragraph(f"<b>Invoice:</b> {escape(sale.invoice_no)} &nbsp; <b>Date:</b> {escape(created_str)}", st["Normal"]),
              Paragraph(f"<b>Customer:</b> {escape(sale.customer.name if sale.customer else 'Walk-in')}", st["Normal"]),
              Paragraph(f"<b>Cashier:</b> {escape(sale.cashier.full_name)}", st["Normal"]),
              Spacer(1, 10)]
    rows = [["SKU", "Item", "Qty", "Unit price", "Total"]]
    for i in sale.items:
        rows.append([i.sku, i.name, fmt_qty(i.qty), fmt_money(i.unit_price), fmt_money(i.line_total)])
    t = Table(rows, repeatRows=1, colWidths=[70, 210, 50, 80, 80])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#12304f")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                           ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                           ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    story += [t, Spacer(1, 10)]
    tot = [["Subtotal", fmt_money(sale.subtotal, cur)], ["Discount", fmt_money(sale.discount, cur)],
           ["Tax", fmt_money(sale.tax, cur)], ["Total payable", fmt_money(sale.total, cur)],
           ["Paid", fmt_money(sale.paid, cur)], ["Outstanding", fmt_money(sale.total - sale.paid, cur)]]
    if sale.change_given and sale.change_given > 0:
        tot.append(["Change returned", fmt_money(sale.change_given, cur)])
    tt = Table(tot, colWidths=[120, 100], hAlign="RIGHT")
    tt.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold")]))
    story.append(tt)
    if business.get("return_policy"):
        story += [Spacer(1, 14), Paragraph(f"<b>Return policy:</b> {escape(business['return_policy'])}", st["Normal"])]
    doc.build(story)
    return buf.getvalue()
