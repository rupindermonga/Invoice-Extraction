"""Lender Package Autopilot — generates a professional, lender-ready draw submission PDF.

Structure:
  Page 1    : Cover page (project, draw, prepared by, branding)
  Page 2    : AI-generated cover letter (Gemini) or template fallback
  Page 3    : Executive summary (totals, holdback, compliance status)
  Page 4+   : Invoice schedule (full backup table)
  Page N    : Holdback schedule
  Page N+1  : Cost category breakdown (budget vs invoiced vs this draw)
  Page N+2  : Compliance checklist (what's ready / blocking / warning)
  Last page : Certification & signature block
"""
from __future__ import annotations
import io
import json
import logging
from datetime import datetime
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# ─── Brand palette ────────────────────────────────────────────────────────────
_BRAND_DARK  = (15/255,  23/255,  42/255)   # #0f172a  slate-900
_BRAND_BLUE  = (0/255, 172/255, 255/255)    # #00acff  finel blue
_BRAND_MID   = (0/255, 113/255, 153/255)    # #007199  finel blue-800
_WHITE       = (1.0, 1.0, 1.0)
_LIGHT_GREY  = (0.96, 0.97, 0.98)
_MID_GREY    = (0.55, 0.60, 0.65)
_DARK_GREY   = (0.20, 0.24, 0.28)
_GREEN       = (0.14, 0.64, 0.29)
_ORANGE      = (0.90, 0.45, 0.05)
_RED         = (0.75, 0.10, 0.10)


def _fmt(amount: float | None, currency: str = "CAD") -> str:
    if amount is None:
        return "—"
    return f"${amount:,.2f}"


def _today_str() -> str:
    return datetime.utcnow().strftime("%B %d, %Y")


# ─── AI Cover Letter ──────────────────────────────────────────────────────────

def _generate_cover_letter(project: Any, draw: Any, invoices: List[Any], db: Any) -> str:
    """Use Gemini to write a professional 3-paragraph cover letter for this draw package."""
    from .gemini import _env_keys
    from ..models import GeminiApiKey

    api_keys = _env_keys()
    if not api_keys:
        db_keys = db.query(GeminiApiKey).filter(GeminiApiKey.is_active == True).order_by(GeminiApiKey.priority).all()
        api_keys = [k.key_value for k in db_keys]

    total_submitted = sum(i.lender_submitted_amt or i.total_due or 0 for i in invoices)
    holdback_total = sum(
        round((i.subtotal or i.total_due or 0) * (i.holdback_pct or 0) / 100, 2)
        for i in invoices if not i.holdback_released
    )
    net_to_fund = round(total_submitted - holdback_total, 2)
    vendor_count = len({i.vendor_name for i in invoices if i.vendor_name})

    if api_keys:
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_keys[0])
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = f"""Write a professional construction draw request cover letter for submission to a lender.
Use formal Canadian business letter tone. Three paragraphs, maximum 200 words total.

Context:
- Project: {project.name}{(' ('+project.code+')') if project.code else ''}
- Client/Owner: {project.client or 'the owner'}
- Draw Number: {draw.draw_number}
- Submission Date: {draw.submission_date or _today_str()}
- Total Amount Requested: ${total_submitted:,.2f} CAD
- Net to Fund (after holdback): ${net_to_fund:,.2f} CAD
- Number of Invoices: {len(invoices)}
- Number of Vendors: {vendor_count}
- Draw Status: {draw.status}
- Notes: {draw.notes or 'None'}

Paragraph 1: State purpose of the draw request and reference the project.
Paragraph 2: Summarize the work completed and costs incurred this draw period.
Paragraph 3: Confirm all supporting documentation is attached and request timely review.

Output only the letter body text — no salutation, no closing, no placeholders in brackets."""
            resp = model.generate_content(prompt)
            return resp.text.strip()
        except Exception as e:
            logger.warning("Gemini cover letter failed: %s", e)

    # Template fallback
    return (
        f"We are pleased to submit Draw Request No. {draw.draw_number} in connection with the "
        f"{project.name} project{(' located at ' + project.address) if project.address else ''}. "
        f"This draw package has been prepared in accordance with our loan agreement and includes "
        f"all required supporting documentation for your review.\n\n"
        f"The draw covers costs totalling ${total_submitted:,.2f} CAD, representing work completed "
        f"and materials supplied by {vendor_count} vendor(s) during the current draw period. "
        f"All invoices have been reviewed and approved internally prior to submission. "
        f"Statutory holdback of ${holdback_total:,.2f} has been retained in accordance with applicable "
        f"construction legislation, resulting in a net funding request of ${net_to_fund:,.2f} CAD.\n\n"
        f"We confirm that all backup documentation, including invoices, lien waivers where available, "
        f"and cost schedules, is attached hereto. We respectfully request your timely review and "
        f"approval of this draw request. Please do not hesitate to contact us should you require "
        f"any additional information."
    )


# ─── PDF Generator ────────────────────────────────────────────────────────────

def generate_lender_package_pdf(
    project: Any,
    draw: Any,
    invoices: List[Any],
    categories: List[Any],
    allocations_by_cat: dict,
    lien_waivers: List[Any],
    subcontractors: List[Any],
    documents: List[Any],
    prepared_by: str,
    db: Any,
) -> bytes:
    """Generate a complete lender draw package as PDF bytes."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable, KeepTogether
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
    from reportlab.platypus.flowables import Flowable

    PAGE_W, PAGE_H = letter
    MARGIN = 0.75 * inch

    buf = io.BytesIO()

    # ── Styles ─────────────────────────────────────────────────────────────────
    styles = getSampleStyleSheet()
    brand_dark  = colors.Color(*_BRAND_DARK)
    brand_blue  = colors.Color(*_BRAND_BLUE)
    brand_mid   = colors.Color(*_BRAND_MID)
    light_grey  = colors.Color(*_LIGHT_GREY)
    mid_grey    = colors.Color(*_MID_GREY)
    dark_grey   = colors.Color(*_DARK_GREY)
    green_color = colors.Color(*_GREEN)
    orange_color= colors.Color(*_ORANGE)
    red_color   = colors.Color(*_RED)

    def _style(name, **kw):
        base = styles["Normal"]
        return ParagraphStyle(name, parent=base, **kw)

    s_title     = _style("Title",    fontSize=26, textColor=brand_dark, fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=6)
    s_subtitle  = _style("Subtitle", fontSize=13, textColor=brand_mid,  fontName="Helvetica",      alignment=TA_CENTER, spaceAfter=4)
    s_h1        = _style("H1",       fontSize=14, textColor=brand_dark, fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=6)
    s_h2        = _style("H2",       fontSize=11, textColor=brand_dark, fontName="Helvetica-Bold", spaceBefore=8,  spaceAfter=4)
    s_body      = _style("Body",     fontSize=9,  textColor=dark_grey,  fontName="Helvetica",      spaceAfter=4, leading=14)
    s_body_just = _style("BodyJ",    fontSize=9,  textColor=dark_grey,  fontName="Helvetica",      spaceAfter=4, leading=14, alignment=TA_JUSTIFY)
    s_small     = _style("Small",    fontSize=7.5,textColor=mid_grey,   fontName="Helvetica",      spaceAfter=2)
    s_label     = _style("Label",    fontSize=8,  textColor=mid_grey,   fontName="Helvetica-Bold", spaceAfter=2)
    s_value     = _style("Value",    fontSize=9,  textColor=brand_dark, fontName="Helvetica-Bold", spaceAfter=4)
    s_center    = _style("Center",   fontSize=9,  textColor=dark_grey,  fontName="Helvetica",      alignment=TA_CENTER)
    s_right     = _style("Right",    fontSize=9,  textColor=dark_grey,  fontName="Helvetica",      alignment=TA_RIGHT)
    s_right_b   = _style("RightB",   fontSize=9,  textColor=brand_dark, fontName="Helvetica-Bold", alignment=TA_RIGHT)
    s_green     = _style("Green",    fontSize=9,  textColor=green_color,fontName="Helvetica-Bold", alignment=TA_RIGHT)
    s_red       = _style("Red",      fontSize=9,  textColor=red_color,  fontName="Helvetica-Bold", alignment=TA_RIGHT)
    s_confid    = _style("Confid",   fontSize=7,  textColor=mid_grey,   fontName="Helvetica",      alignment=TA_CENTER)

    # ── Page template with header/footer ──────────────────────────────────────
    doc_title = f"{project.name} — Draw {draw.draw_number} Package"

    def _on_page(canvas, doc):
        canvas.saveState()
        page_num = doc.page

        # Top rule (brand blue)
        canvas.setStrokeColor(brand_blue)
        canvas.setLineWidth(2)
        canvas.line(MARGIN, PAGE_H - 0.45*inch, PAGE_W - MARGIN, PAGE_H - 0.45*inch)

        # Header text (skip on cover)
        if page_num > 1:
            canvas.setFont("Helvetica-Bold", 7.5)
            canvas.setFillColor(brand_mid)
            canvas.drawString(MARGIN, PAGE_H - 0.35*inch, doc_title)
            canvas.setFont("Helvetica", 7.5)
            canvas.setFillColor(mid_grey)
            canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.35*inch, f"CONFIDENTIAL")

        # Bottom rule + footer
        canvas.setStrokeColor(light_grey)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, 0.55*inch, PAGE_W - MARGIN, 0.55*inch)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(mid_grey)
        canvas.drawString(MARGIN, 0.35*inch, f"Generated by Finel AI · {_today_str()}")
        canvas.drawCentredString(PAGE_W/2, 0.35*inch, "CONFIDENTIAL — For Lender Use Only")
        canvas.drawRightString(PAGE_W - MARGIN, 0.35*inch, f"Page {page_num}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=0.65*inch, bottomMargin=0.75*inch,
        title=doc_title,
        author="Finel AI",
        subject=f"Draw {draw.draw_number} Lender Package",
    )

    story = []

    # ── Computed totals ────────────────────────────────────────────────────────
    total_invoiced   = sum(i.total_due or 0 for i in invoices)
    total_submitted  = sum(i.lender_submitted_amt or i.total_due or 0 for i in invoices)
    total_approved   = sum(i.lender_approved_amt or 0 for i in invoices if i.lender_approved_amt)
    holdback_held    = sum(
        round((i.subtotal or i.total_due or 0) * (i.holdback_pct or 0) / 100, 2)
        for i in invoices if not i.holdback_released
    )
    net_to_fund      = round(total_submitted - holdback_held, 2)
    today            = _today_str()
    submission_date  = draw.submission_date or datetime.utcnow().strftime("%Y-%m-%d")

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 1: COVER PAGE
    # ─────────────────────────────────────────────────────────────────────────

    # Brand colour block at top (drawn via canvas on first page — use a spacer)
    story.append(Spacer(1, 1.1*inch))

    # Finel AI wordmark
    story.append(Paragraph("Finel AI", _style("Brand", fontSize=11, textColor=brand_blue, fontName="Helvetica-Bold", alignment=TA_CENTER)))
    story.append(Spacer(1, 0.1*inch))

    story.append(HRFlowable(width="100%", thickness=2, color=brand_blue, spaceAfter=18))

    story.append(Paragraph("DRAW REQUEST PACKAGE", s_title))
    story.append(Paragraph("Prepared for Lender Review", s_subtitle))
    story.append(Spacer(1, 0.3*inch))

    # Cover info block
    cover_data = [
        [Paragraph("PROJECT", s_label),   Paragraph(project.name, s_value)],
        [Paragraph("PROJECT CODE", s_label), Paragraph(project.code or "—", s_value)],
        [Paragraph("CLIENT / OWNER", s_label), Paragraph(project.client or "—", s_value)],
        [Paragraph("PROJECT ADDRESS", s_label), Paragraph(project.address or "—", s_value)],
        [Paragraph("DRAW NUMBER", s_label), Paragraph(f"Draw {draw.draw_number}", s_value)],
        [Paragraph("SUBMISSION DATE", s_label), Paragraph(submission_date, s_value)],
        [Paragraph("DRAW STATUS", s_label), Paragraph(draw.status.upper(), s_value)],
        [Paragraph("CURRENCY", s_label), Paragraph(f"{project.currency or 'CAD'} (FX Rate: {draw.fx_rate:.4f})", s_value)],
        [Paragraph("PREPARED BY", s_label), Paragraph(prepared_by, s_value)],
        [Paragraph("GENERATED", s_label), Paragraph(today, s_value)],
    ]
    cover_tbl = Table(cover_data, colWidths=[1.8*inch, 4.2*inch])
    cover_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,-1), light_grey),
        ("PADDING", (0,0), (-1,-1), 7),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, light_grey]),
        ("BOX", (0,0), (-1,-1), 0.5, colors.Color(0.88,0.90,0.92)),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.Color(0.88,0.90,0.92)),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(cover_tbl)
    story.append(Spacer(1, 0.4*inch))

    # Summary highlight bar
    summary_data = [[
        Paragraph(f"${total_submitted:,.2f}\nRequested", _style("SumBox", fontSize=11, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER, leading=16)),
        Paragraph(f"${holdback_held:,.2f}\nHoldback", _style("SumBox2", fontSize=11, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER, leading=16)),
        Paragraph(f"${net_to_fund:,.2f}\nNet to Fund", _style("SumBox3", fontSize=12, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER, leading=16)),
        Paragraph(f"{len(invoices)}\nInvoices", _style("SumBox4", fontSize=11, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER, leading=16)),
    ]]
    summary_tbl = Table(summary_data, colWidths=[1.55*inch]*4)
    summary_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,0), brand_mid),
        ("BACKGROUND", (1,0), (1,0), colors.Color(*_ORANGE)),
        ("BACKGROUND", (2,0), (2,0), brand_dark),
        ("BACKGROUND", (3,0), (3,0), colors.Color(0.25,0.55,0.75)),
        ("PADDING", (0,0), (-1,-1), 12),
        ("BOX", (0,0), (-1,-1), 1, brand_blue),
        ("INNERGRID", (0,0), (-1,-1), 0.5, brand_blue),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(summary_tbl)
    story.append(Spacer(1, 0.5*inch))
    story.append(Paragraph("CONFIDENTIAL — This document contains financial information prepared for lender review only. Unauthorized distribution is prohibited.", s_confid))

    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 2: COVER LETTER
    # ─────────────────────────────────────────────────────────────────────────
    story.append(Paragraph("Cover Letter", s_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=brand_blue, spaceAfter=12))

    story.append(Paragraph(today, s_body))
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph("To Whom It May Concern,", s_body))
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph(f"Re: Draw {draw.draw_number} — {project.name}", _style("Re", fontSize=10, fontName="Helvetica-Bold", textColor=brand_dark, spaceAfter=8)))
    story.append(Spacer(1, 0.05*inch))

    cover_letter_text = _generate_cover_letter(project, draw, invoices, db)
    for para in cover_letter_text.split("\n\n"):
        if para.strip():
            story.append(Paragraph(para.strip(), s_body_just))
            story.append(Spacer(1, 0.06*inch))

    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("Yours truly,", s_body))
    story.append(Spacer(1, 0.5*inch))
    story.append(Paragraph(f"{prepared_by}", _style("Sig", fontSize=10, fontName="Helvetica-Bold", textColor=brand_dark)))
    story.append(Paragraph(f"{project.client or project.name}", s_body))
    story.append(Paragraph(today, s_small))

    if draw.notes:
        story.append(Spacer(1, 0.2*inch))
        story.append(Paragraph("Draw Notes:", s_h2))
        story.append(Paragraph(draw.notes, s_body))

    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 3: EXECUTIVE SUMMARY
    # ─────────────────────────────────────────────────────────────────────────
    story.append(Paragraph("Executive Summary", s_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=brand_blue, spaceAfter=10))

    exec_data = [
        ["", "Amount (CAD)"],
        ["Total Invoiced (vendor amount)", _fmt(total_invoiced)],
        ["Total Submitted to Lender", _fmt(total_submitted)],
        ["Holdback Withheld (statutory)", _fmt(holdback_held)],
        ["Net Funding Requested", _fmt(net_to_fund)],
        ["Previously Approved (prior draws)", "See draw history"],
    ]
    if total_approved > 0:
        exec_data[5] = ["Lender Approved (this draw)", _fmt(total_approved)]

    exec_tbl = Table(exec_data, colWidths=[4.0*inch, 2.2*inch])
    exec_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), brand_dark),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("BACKGROUND", (0,4), (-1,4), brand_dark),
        ("TEXTCOLOR", (0,4), (-1,4), colors.white),
        ("FONTNAME", (0,4), (-1,4), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, light_grey]),
        ("GRID", (0,0), (-1,-1), 0.25, colors.Color(0.88,0.90,0.92)),
        ("PADDING", (0,0), (-1,-1), 7),
        ("ALIGN", (1,0), (1,-1), "RIGHT"),
        ("FONTNAME", (0,1), (0,-1), "Helvetica"),
    ]))
    story.append(exec_tbl)
    story.append(Spacer(1, 0.2*inch))

    # Invoice count by approval status
    pending = sum(1 for i in invoices if i.approval_status == "pending")
    approved = sum(1 for i in invoices if i.approval_status == "approved")
    rejected = sum(1 for i in invoices if i.approval_status == "rejected")
    lender_approved_count = sum(1 for i in invoices if i.lender_status == "approved")
    lender_pending_count = sum(1 for i in invoices if i.lender_status == "pending")

    status_data = [
        ["Invoice Status", "Count"],
        ["Internally Approved", str(approved)],
        ["Pending Internal Approval", str(pending)],
        ["Rejected Internally", str(rejected)],
        ["Lender Status: Approved", str(lender_approved_count)],
        ["Lender Status: Pending", str(lender_pending_count)],
    ]
    status_tbl = Table(status_data, colWidths=[4.0*inch, 2.2*inch])
    status_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), brand_mid),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, light_grey]),
        ("GRID", (0,0), (-1,-1), 0.25, colors.Color(0.88,0.90,0.92)),
        ("PADDING", (0,0), (-1,-1), 7),
        ("ALIGN", (1,0), (1,-1), "RIGHT"),
    ]))
    story.append(status_tbl)

    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE 4+: INVOICE SCHEDULE
    # ─────────────────────────────────────────────────────────────────────────
    story.append(Paragraph("Invoice Schedule", s_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=brand_blue, spaceAfter=8))
    story.append(Paragraph(
        f"All {len(invoices)} invoices submitted in Draw {draw.draw_number}. "
        "Holdback calculated per invoice. Amounts in CAD unless otherwise noted.",
        s_small))
    story.append(Spacer(1, 0.1*inch))

    # Table header
    inv_header = [
        Paragraph("#", _style("TH", fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER)),
        Paragraph("Invoice #", _style("TH", fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph("Date", _style("TH", fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph("Vendor", _style("TH", fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph("Subtotal", _style("TH", fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("Tax", _style("TH", fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("Total", _style("TH", fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("Submitted", _style("TH", fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("Holdback", _style("TH", fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("Net", _style("TH", fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("Status", _style("TH", fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER)),
    ]
    inv_rows = [inv_header]
    col_widths = [0.28*inch, 0.75*inch, 0.65*inch, 1.55*inch,
                  0.72*inch, 0.58*inch, 0.72*inch, 0.72*inch, 0.65*inch, 0.72*inch, 0.62*inch]

    run_submitted = 0.0
    run_holdback  = 0.0
    run_net       = 0.0

    for idx, inv in enumerate(sorted(invoices, key=lambda i: i.invoice_date or ""), start=1):
        subtotal  = inv.subtotal or (inv.total_due or 0)
        tax_amt   = inv.tax_total or 0
        total     = inv.total_due or 0
        submitted = inv.lender_submitted_amt or total
        hb_pct    = inv.holdback_pct or 0
        hb_amt    = round((inv.subtotal or total) * hb_pct / 100, 2) if not inv.holdback_released else 0.0
        net       = round(submitted - hb_amt, 2)
        run_submitted += submitted
        run_holdback  += hb_amt
        run_net       += net

        status_str = inv.lender_status or "pending"
        status_color = green_color if status_str == "approved" else (red_color if status_str == "rejected" else mid_grey)

        row = [
            Paragraph(str(idx), s_center),
            Paragraph(inv.invoice_number or "—", _style("Cell", fontSize=7.5, fontName="Helvetica")),
            Paragraph(inv.invoice_date or "—", _style("Cell", fontSize=7.5, fontName="Helvetica")),
            Paragraph((inv.vendor_name or "Unknown")[:28], _style("Cell", fontSize=7.5, fontName="Helvetica")),
            Paragraph(_fmt(subtotal), _style("NumC", fontSize=7.5, fontName="Helvetica", alignment=TA_RIGHT)),
            Paragraph(_fmt(tax_amt) if tax_amt else "—", _style("NumC", fontSize=7.5, fontName="Helvetica", alignment=TA_RIGHT)),
            Paragraph(_fmt(total), _style("NumC", fontSize=7.5, fontName="Helvetica", alignment=TA_RIGHT)),
            Paragraph(_fmt(submitted), _style("NumC", fontSize=7.5, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=brand_dark)),
            Paragraph(_fmt(hb_amt) if hb_amt else "—", _style("NumC", fontSize=7.5, fontName="Helvetica", alignment=TA_RIGHT, textColor=orange_color)),
            Paragraph(_fmt(net), _style("NumC", fontSize=7.5, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=green_color)),
            Paragraph(status_str.upper(), _style("Stat", fontSize=7, fontName="Helvetica-Bold", alignment=TA_CENTER, textColor=status_color)),
        ]
        inv_rows.append(row)

    # Totals row
    inv_rows.append([
        Paragraph("", s_center),
        Paragraph("TOTAL", _style("Tot", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph("", s_center), Paragraph("", s_center), Paragraph("", s_center),
        Paragraph("", s_center), Paragraph("", s_center),
        Paragraph(_fmt(run_submitted), _style("TotN", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph(_fmt(run_holdback), _style("TotN", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph(_fmt(run_net), _style("TotN", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("", s_center),
    ])

    inv_tbl = Table(inv_rows, colWidths=col_widths, repeatRows=1)
    inv_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), brand_dark),
        ("BACKGROUND", (0,-1), (-1,-1), brand_mid),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, light_grey]),
        ("GRID", (0,0), (-1,-1), 0.25, colors.Color(0.88,0.90,0.92)),
        ("PADDING", (0,0), (-1,-1), 4),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("FONTSIZE", (0,0), (-1,-1), 7.5),
    ]))
    story.append(inv_tbl)
    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE N: HOLDBACK SCHEDULE
    # ─────────────────────────────────────────────────────────────────────────
    story.append(Paragraph("Holdback / Retainage Schedule", s_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=brand_blue, spaceAfter=8))
    story.append(Paragraph(
        "Holdback withheld per invoice in accordance with applicable provincial construction legislation.",
        s_small))
    story.append(Spacer(1, 0.1*inch))

    hb_header = [
        Paragraph("Invoice #", _style("TH2", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph("Vendor", _style("TH2", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph("Invoice Date", _style("TH2", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER)),
        Paragraph("Invoice Total", _style("TH2", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("Holdback %", _style("TH2", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("Holdback $", _style("TH2", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("Released", _style("TH2", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER)),
        Paragraph("Release Date", _style("TH2", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER)),
    ]
    hb_rows = [hb_header]
    total_hb_held = 0.0
    total_hb_released = 0.0

    for inv in sorted(invoices, key=lambda i: i.invoice_date or ""):
        hb_pct = inv.holdback_pct or 0
        if hb_pct == 0:
            continue
        base = inv.subtotal or inv.total_due or 0
        hb_amt = round(base * hb_pct / 100, 2)
        released = bool(inv.holdback_released)
        if released:
            total_hb_released += hb_amt
        else:
            total_hb_held += hb_amt
        hb_rows.append([
            Paragraph(inv.invoice_number or "—", _style("HC", fontSize=8, fontName="Helvetica")),
            Paragraph((inv.vendor_name or "Unknown")[:30], _style("HC", fontSize=8, fontName="Helvetica")),
            Paragraph(inv.invoice_date or "—", _style("HC", fontSize=8, fontName="Helvetica", alignment=TA_CENTER)),
            Paragraph(_fmt(inv.total_due), _style("HC", fontSize=8, fontName="Helvetica", alignment=TA_RIGHT)),
            Paragraph(f"{hb_pct:.1f}%", _style("HC", fontSize=8, fontName="Helvetica", alignment=TA_RIGHT)),
            Paragraph(_fmt(hb_amt), _style("HC", fontSize=8, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=colors.Color(*_ORANGE))),
            Paragraph("YES" if released else "NO", _style("HC", fontSize=8, fontName="Helvetica-Bold", alignment=TA_CENTER, textColor=green_color if released else mid_grey)),
            Paragraph(inv.holdback_released_date or "—", _style("HC", fontSize=8, fontName="Helvetica", alignment=TA_CENTER)),
        ])

    if len(hb_rows) == 1:
        story.append(Paragraph("No holdback applicable to invoices in this draw.", s_body))
    else:
        hb_rows.append([
            Paragraph("TOTAL", _style("TR", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white)),
            Paragraph("", s_center), Paragraph("", s_center), Paragraph("", s_center), Paragraph("", s_center),
            Paragraph(_fmt(total_hb_held + total_hb_released), _style("TR", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
            Paragraph("", s_center),
            Paragraph("", s_center),
        ])
        hb_tbl = Table(hb_rows, colWidths=[0.85*inch, 1.9*inch, 0.85*inch, 0.85*inch, 0.75*inch, 0.85*inch, 0.75*inch, 0.85*inch], repeatRows=1)
        hb_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), brand_dark),
            ("BACKGROUND", (0,-1), (-1,-1), brand_mid),
            ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, light_grey]),
            ("GRID", (0,0), (-1,-1), 0.25, colors.Color(0.88,0.90,0.92)),
            ("PADDING", (0,0), (-1,-1), 5),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ]))
        story.append(hb_tbl)

        # Holdback summary box
        story.append(Spacer(1, 0.15*inch))
        hb_sum = Table([
            [Paragraph("TOTAL HOLDBACK HELD", _style("HS", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)),
             Paragraph(_fmt(total_hb_held), _style("HS", fontSize=11, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT))],
            [Paragraph("TOTAL HOLDBACK RELEASED", _style("HS2", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)),
             Paragraph(_fmt(total_hb_released), _style("HS2", fontSize=11, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT))],
        ], colWidths=[4.0*inch, 2.6*inch])
        hb_sum.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.Color(*_ORANGE)),
            ("BACKGROUND", (0,1), (-1,1), green_color),
            ("PADDING", (0,0), (-1,-1), 10),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ]))
        story.append(hb_sum)

    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE N+1: COST CATEGORY BREAKDOWN
    # ─────────────────────────────────────────────────────────────────────────
    story.append(Paragraph("Cost Category Summary", s_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=brand_blue, spaceAfter=8))
    story.append(Paragraph(
        "Approved project budget vs invoiced to date vs amount submitted in this draw, by cost category.",
        s_small))
    story.append(Spacer(1, 0.1*inch))

    cat_header = [
        Paragraph("Cost Category", _style("CH", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph("Budget (CAD)", _style("CH", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("Invoiced to Date", _style("CH", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("% Used", _style("CH", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("This Draw", _style("CH", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph("Remaining", _style("CH", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
    ]
    cat_rows = [cat_header]
    tot_budget = tot_invoiced = tot_this_draw = 0.0

    for cat in categories:
        invoiced_total = allocations_by_cat.get(cat.id, 0.0)
        pct = round(invoiced_total / cat.budget * 100, 1) if cat.budget else 0

        # Amount from this draw specifically
        this_draw_alloc = 0.0
        for inv in invoices:
            for alloc in getattr(inv, '_allocs', []):
                if alloc.category_id == cat.id:
                    this_draw_alloc += alloc.amount

        remaining = cat.budget - invoiced_total
        pct_str = f"{pct:.1f}%"
        pct_color = red_color if pct >= 100 else (orange_color if pct >= 85 else dark_grey)

        cat_rows.append([
            Paragraph(cat.name, _style("CC", fontSize=8, fontName="Helvetica")),
            Paragraph(_fmt(cat.budget), _style("CC", fontSize=8, fontName="Helvetica", alignment=TA_RIGHT)),
            Paragraph(_fmt(invoiced_total), _style("CC", fontSize=8, fontName="Helvetica", alignment=TA_RIGHT)),
            Paragraph(pct_str, _style("CC", fontSize=8, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=pct_color)),
            Paragraph(_fmt(this_draw_alloc) if this_draw_alloc else "—", _style("CC", fontSize=8, fontName="Helvetica", alignment=TA_RIGHT)),
            Paragraph(_fmt(remaining), _style("CC", fontSize=8, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=red_color if remaining < 0 else green_color)),
        ])
        tot_budget += cat.budget
        tot_invoiced += invoiced_total
        tot_this_draw += this_draw_alloc

    cat_rows.append([
        Paragraph("TOTAL", _style("CT", fontSize=8.5, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph(_fmt(tot_budget), _style("CT", fontSize=8.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph(_fmt(tot_invoiced), _style("CT", fontSize=8.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph(f"{round(tot_invoiced/tot_budget*100,1):.1f}%" if tot_budget else "—", _style("CT", fontSize=8.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph(_fmt(tot_this_draw) if tot_this_draw else "—", _style("CT", fontSize=8.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph(_fmt(tot_budget - tot_invoiced), _style("CT", fontSize=8.5, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
    ])

    cat_tbl = Table(cat_rows, colWidths=[2.1*inch, 1.1*inch, 1.15*inch, 0.7*inch, 1.0*inch, 1.0*inch], repeatRows=1)
    cat_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), brand_dark),
        ("BACKGROUND", (0,-1), (-1,-1), brand_mid),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, light_grey]),
        ("GRID", (0,0), (-1,-1), 0.25, colors.Color(0.88,0.90,0.92)),
        ("PADDING", (0,0), (-1,-1), 5),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(cat_tbl)
    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────────────────
    # PAGE N+2: COMPLIANCE CHECKLIST
    # ─────────────────────────────────────────────────────────────────────────
    story.append(Paragraph("Draw Submission Compliance Checklist", s_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=brand_blue, spaceAfter=8))

    from .ai_project import draw_readiness as _draw_readiness
    readiness = _draw_readiness(draw, invoices, lien_waivers, subcontractors, documents)

    # Readiness score bar
    score = readiness["readiness_score"]
    score_color = green_color if score >= 80 else (orange_color if score >= 50 else red_color)
    score_label = "READY FOR SUBMISSION" if score >= 80 else ("PROCEED WITH CAUTION" if score >= 50 else "NOT READY — ACTION REQUIRED")

    score_tbl = Table([[
        Paragraph("Readiness Score", _style("RS", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph(f"{score}/100", _style("RS2", fontSize=14, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
        Paragraph(score_label, _style("RS3", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT)),
    ]], colWidths=[2.0*inch, 1.0*inch, 3.6*inch])
    score_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), score_color),
        ("PADDING", (0,0), (-1,-1), 10),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(score_tbl)
    story.append(Spacer(1, 0.15*inch))

    # Checklist items
    check_header = [
        Paragraph("Check", _style("CHK", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph("Item", _style("CHK", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white)),
        Paragraph("Detail", _style("CHK", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white)),
    ]
    check_rows = [check_header]
    for item in readiness["checklist"]:
        if item["status"] == "ready":
            icon, row_bg = "✓", colors.white
        elif item["status"] == "blocking":
            icon, row_bg = "✗", colors.Color(1.0, 0.94, 0.94)
        else:
            icon, row_bg = "!", colors.Color(1.0, 0.97, 0.88)
        check_rows.append([
            Paragraph(icon, _style("IC", fontSize=11, fontName="Helvetica-Bold",
                                   textColor=green_color if icon=="✓" else (red_color if icon=="✗" else orange_color), alignment=TA_CENTER)),
            Paragraph(item["label"], _style("CL", fontSize=8.5, fontName="Helvetica-Bold", textColor=brand_dark)),
            Paragraph(item["detail"], _style("CD", fontSize=8, fontName="Helvetica", textColor=dark_grey)),
        ])

    check_tbl = Table(check_rows, colWidths=[0.4*inch, 1.8*inch, 4.4*inch], repeatRows=1)
    _check_styles = [
        ("BACKGROUND", (0,0), (-1,0), brand_dark),
        ("GRID", (0,0), (-1,-1), 0.25, colors.Color(0.88,0.90,0.92)),
        ("PADDING", (0,0), (-1,-1), 6),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]
    for i, item in enumerate(readiness["checklist"], start=1):
        if item["status"] == "blocking":
            _check_styles.append(("BACKGROUND", (0,i), (-1,i), colors.Color(1.0, 0.94, 0.94)))
        elif item["status"] == "warning":
            _check_styles.append(("BACKGROUND", (0,i), (-1,i), colors.Color(1.0, 0.97, 0.88)))
    check_tbl.setStyle(TableStyle(_check_styles))
    story.append(check_tbl)

    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────────────────
    # LAST PAGE: CERTIFICATION
    # ─────────────────────────────────────────────────────────────────────────
    story.append(Paragraph("Certification & Declaration", s_h1))
    story.append(HRFlowable(width="100%", thickness=1, color=brand_blue, spaceAfter=12))

    cert_text = (
        f"The undersigned hereby certifies that the costs included in Draw Request No. {draw.draw_number} "
        f"for the {project.name} project are true and accurate, have been incurred in connection with the "
        f"project, and are in accordance with the approved project budget and loan agreement. "
        f"All vendors have been or will be paid from the proceeds of this draw. "
        f"To the best of the signatory's knowledge, there are no outstanding claims, liens, or encumbrances "
        f"against the project that would impair the lender's security interest, other than as disclosed herein."
    )
    story.append(Paragraph(cert_text, s_body_just))
    story.append(Spacer(1, 0.4*inch))

    sig_data = [
        [Paragraph("Authorized Signature", s_label),   Paragraph("Date", s_label),   Paragraph("Title / Position", s_label)],
        [Paragraph("", s_body),                        Paragraph(today, s_body),       Paragraph("", s_body)],
        [HRFlowable(width="100%", thickness=0.5, color=mid_grey), HRFlowable(width="100%", thickness=0.5, color=mid_grey), HRFlowable(width="100%", thickness=0.5, color=mid_grey)],
        [Paragraph(prepared_by, _style("SN", fontSize=9, fontName="Helvetica-Bold", textColor=brand_dark)),
         Paragraph(today, s_small),
         Paragraph(project.client or project.name, s_small)],
    ]
    sig_tbl = Table(sig_data, colWidths=[2.4*inch, 1.6*inch, 2.6*inch])
    sig_tbl.setStyle(TableStyle([
        ("PADDING", (0,0), (-1,-1), 8),
        ("VALIGN", (0,0), (-1,-1), "BOTTOM"),
        ("TOPPADDING", (0,1), (-1,1), 32),
    ]))
    story.append(sig_tbl)
    story.append(Spacer(1, 0.3*inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=light_grey, spaceAfter=8))
    story.append(Paragraph(
        f"This document was generated by Finel AI Project Finance on {today}. "
        "It is intended solely for the use of the designated lender and should not be reproduced or distributed without authorization.",
        s_confid))

    # ── Build ──────────────────────────────────────────────────────────────────
    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()
