"""Render only the server-calculated, snapshotted invoice. No database or network I/O."""
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app_backend.services.service_08_financial_management.logic.accounts_receivables_calculations import decimal_value


ASSETS = Path(__file__).with_name("assets")
pdfmetrics.registerFont(TTFont("ARDejaVu", str(ASSETS / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("ARDejaVuBold", str(ASSETS / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFontFamily("ARDejaVu", normal="ARDejaVu", bold="ARDejaVuBold")


def integer_words(value):
    small = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
    tens = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()
    if value < 20:
        return small[value]
    if value < 100:
        return tens[value // 10] + (" " + small[value % 10] if value % 10 else "")
    if value < 1000:
        return small[value // 100] + " hundred" + (" " + integer_words(value % 100) if value % 100 else "")
    for divisor, label in ((10**15, "quadrillion"), (10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if value >= divisor:
            return integer_words(value // divisor) + " " + label + (" " + integer_words(value % divisor) if value % divisor else "")


def amount_in_words(amount, currency="AED"):
    amount = decimal_value(amount, "invoice total")
    whole = int(amount)
    fraction = int((amount - whole) * 100)
    if currency == "AED":
        return (f"{integer_words(whole)} {'dirham' if whole == 1 else 'dirhams'} and "
                f"{integer_words(fraction)} fils only").upper()
    return f"{currency} {integer_words(whole)} and {fraction:02d}/100 only".upper()


def generate_ar_invoice_document(invoice):
    identity = invoice["ar_invoice_identity_snapshot"]
    org, customer, config = (identity[key] for key in ("organization", "customer", "invoice_configuration"))
    # Saved AR keys (including explicit blanks) remain authoritative for historical
    # snapshots. Also accept revised contact keys without changing the snapshot.
    customer_phones = [
        customer.get("cust_phone_primary", customer.get("procurement_head_phone_primary")),
        customer.get("cust_phone_secondary", customer.get("procurement_head_phone_secondary")),
    ]
    stream = BytesIO()
    style = ParagraphStyle("AR", fontName="ARDejaVu", fontSize=8, leading=11, spaceAfter=4)
    heading = ParagraphStyle("Heading", parent=style, fontName="ARDejaVuBold", fontSize=18, leading=23, textColor=colors.HexColor("#164a59"))
    right = ParagraphStyle("Right", parent=style, alignment=TA_RIGHT)

    def p(value, text_style=style):
        return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), text_style)

    def amt(value):
        return f"{Decimal(str(value)):,.2f}"

    story = [Image(str(ASSETS / "urban_express_logo.png"), width=87 * mm, height=20 * mm, hAlign="LEFT"),
             Spacer(1, 5 * mm), p("TAX INVOICE", heading), p(org["org_name"]), p(org.get("org_address")),
             p(f"TRN: {config['organization_trn']}"), Spacer(1, 3 * mm)]
    bill_to = [p("BILL TO"), p(customer["cust_name"]), p(customer.get("cust_billing_address")),
               p("Telephone: " + " / ".join(filter(None, customer_phones))),
               p(f"TRN: {customer.get('cust_tax_registration_number') or ''}")]
    info = [p(f"Invoice number: {invoice['ar_invoice_number']}"), p(f"Invoice date: {invoice['ar_invoice_date']}"),
            p(f"Due date: {invoice.get('ar_due_date') or ''}"),
            p(f"Billing period: {invoice.get('ar_billing_period_start') or ''} to {invoice.get('ar_billing_period_end') or ''}"),
            p(f"Contract/reference: {invoice.get('ar_contract_number_snapshot') or invoice.get('ar_contract') or ''}"),
            p(f"Currency: {invoice['ar_currency_code']}")]
    details = Table([[bill_to, info]], colWidths=[92 * mm, 86 * mm], hAlign="LEFT")
    details.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.extend([details, Spacer(1, 5 * mm)])
    rows = [[p(title) for title in ("No.", "Description / service period", "Qty / UOM", "Unit rate", "Net", "Tax", "Total")]]
    for line in invoice["lines"]:
        text_parts = [line["ar_line_description"]]
        for key in ("ar_line_vehicle_description", "ar_line_notes"):
            if line.get(key):
                text_parts.append(line[key])
        if line.get("ar_line_service_period_start") or line.get("ar_line_service_period_end"):
            text_parts.append(f"{line.get('ar_line_service_period_start') or ''} to {line.get('ar_line_service_period_end') or ''}")
        if line.get("ar_line_proration_method"):
            text_parts.append(f"Calendar days: {line['ar_line_proration_numerator']:g}/{line['ar_line_proration_denominator']:g}")
        text_parts.append(f"Tax: {line['ar_line_tax_code']} ({Decimal(str(line['ar_line_tax_rate'])).normalize():f}%)")
        rows.append([p(line["ar_line_number"]), p("\n".join(text_parts)),
            p(f"{Decimal(str(line['ar_line_quantity'])):g}\n{(line.get('ar_line_uom') or '').replace('_', ' ')}"),
            *[p(amt(line[key]), right) for key in ("ar_line_unit_rate", "ar_line_net_amount", "ar_line_tax_amount", "ar_line_total_amount")]])
    table = Table(rows, colWidths=[9*mm, 59*mm, 20*mm, 23*mm, 23*mm, 21*mm, 23*mm], repeatRows=1, splitInRow=1, hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5f1f4")),
        ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#b8c7cc")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.extend([table, Spacer(1, 4 * mm)])
    totals = Table([[p(label), p(f"{invoice['ar_currency_code']} {amt(invoice[key])}", right)] for label, key in
                    (("SUBTOTAL", "ar_subtotal_amount"), ("VAT / TAX", "ar_tax_amount"), ("TOTAL INVOICE AMOUNT", "ar_invoice_amount"))],
                   colWidths=[115*mm, 63*mm], hAlign="LEFT")
    story.append(KeepTogether([totals, Spacer(1, 2*mm), p("AMOUNT IN WORDS: " + amount_in_words(invoice["ar_invoice_amount"], invoice["ar_currency_code"]))]))
    story.extend([Spacer(1, 5*mm), p("Bank transfer details"), p(f"Bank: {config['bank_name']}"),
        p(f"Account name: {config['bank_account_name']}"), p(f"Account number: {config['bank_account_number']}"),
        p(f"IBAN: {config['iban']}"), Spacer(1, 4*mm), p(config["signatory_name"]), p(config["signatory_designation"]),
        p(f"For and on behalf of {org['org_name']}"), p(f"Trade license / registration: {org.get('company_registration_number') or ''}")])

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("ARDejaVu", 7)
        canvas.setFillColor(colors.HexColor("#58666c"))
        contact = " | ".join(filter(None, [org.get("org_email_primary"), org.get("org_email_secondary"), org.get("org_phone_primary")]))
        canvas.drawString(16*mm, 13*mm, contact)
        canvas.drawRightString(A4[0]-16*mm, 13*mm, f"Page {doc.page}")
        canvas.restoreState()

    SimpleDocTemplate(stream, pagesize=A4, leftMargin=16*mm, rightMargin=16*mm, topMargin=15*mm,
                      bottomMargin=23*mm, title=f"Invoice {invoice['ar_invoice_number']}", author=org["org_name"]).build(story, onFirstPage=footer, onLaterPages=footer)
    stream.seek(0)
    return stream
