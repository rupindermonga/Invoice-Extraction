"""Lender Risk: Covenant Tracking, Interest Reserve, WIP Report, Credit Committee Pack."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from datetime import datetime, date, timedelta

from ..database import SessionLocal
from ..dependencies import get_current_user, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import (
    LenderCovenant, InterestReserve, InterestReserveDraw,
    Project, Draw, Invoice, CostCategory, InvoiceAllocation, ChangeOrder
)

router = APIRouter(prefix="/api/project", tags=["lender-risk"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_project(project_id: int, user, db: Session) -> Project:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if not proj:
        raise HTTPException(404, "Project not found")
    require_org_member(db, proj.org_id, user.id, FINANCE_READ_ROLES)
    return proj


# ── Covenants ──────────────────────────────────────────────────────────────────

@router.get("/{project_id}/covenants")
def list_covenants(project_id: int, db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    rows = db.query(LenderCovenant).filter(
        LenderCovenant.project_id == project_id
    ).order_by(LenderCovenant.covenant_type, LenderCovenant.name).all()
    result = []
    for c in rows:
        breach = False
        if c.current_value is not None and c.threshold_value is not None:
            if c.threshold_operator == "<=" and c.current_value > c.threshold_value:
                breach = True
            elif c.threshold_operator == ">=" and c.current_value < c.threshold_value:
                breach = True
        result.append({
            "id": c.id, "covenant_type": c.covenant_type, "name": c.name,
            "threshold_value": c.threshold_value, "threshold_operator": c.threshold_operator,
            "current_value": c.current_value, "as_of_date": c.as_of_date,
            "status": "breach" if breach else c.status,
            "notes": c.notes, "created_at": c.created_at.isoformat(),
        })
    return result


@router.post("/{project_id}/covenants")
def create_covenant(project_id: int, body: dict, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    c = LenderCovenant(
        org_id=proj.org_id, project_id=project_id,
        covenant_type=body.get("covenant_type", "other"),
        name=body["name"],
        threshold_value=body.get("threshold_value"),
        threshold_operator=body.get("threshold_operator", "<="),
        current_value=body.get("current_value"),
        as_of_date=body.get("as_of_date"),
        status=body.get("status", "compliant"),
        notes=body.get("notes"),
        created_by=user.id,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"id": c.id, "name": c.name, "status": c.status}


@router.put("/{project_id}/covenants/{covenant_id}")
def update_covenant(project_id: int, covenant_id: int, body: dict,
                    db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    c = db.query(LenderCovenant).filter(
        LenderCovenant.id == covenant_id, LenderCovenant.project_id == project_id
    ).first()
    if not c:
        raise HTTPException(404)
    for field in ["covenant_type", "name", "threshold_value", "threshold_operator",
                  "current_value", "as_of_date", "status", "notes"]:
        if field in body:
            setattr(c, field, body[field])
    c.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/covenants/{covenant_id}")
def delete_covenant(project_id: int, covenant_id: int, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    c = db.query(LenderCovenant).filter(
        LenderCovenant.id == covenant_id, LenderCovenant.project_id == project_id
    ).first()
    if c:
        db.delete(c)
        db.commit()
    return {"ok": True}


# ── Interest Reserve ────────────────────────────────────────────────────────────

@router.get("/{project_id}/interest-reserve")
def get_interest_reserve(project_id: int, db: Session = Depends(get_db),
                         user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    reserve = db.query(InterestReserve).filter(
        InterestReserve.project_id == project_id
    ).first()
    if not reserve:
        return {"exists": False}

    draws = db.query(InterestReserveDraw).filter(
        InterestReserveDraw.reserve_id == reserve.id
    ).order_by(InterestReserveDraw.draw_date.desc()).all()

    drawn = sum(d.amount for d in draws)
    remaining = reserve.reserve_amount - drawn

    # Forecast monthly burn rate (avg of last 3 draws)
    monthly_burn = 0.0
    if draws:
        recent = sorted(draws, key=lambda x: x.draw_date, reverse=True)[:3]
        if len(recent) >= 2:
            monthly_burn = sum(d.amount for d in recent) / len(recent)

    months_remaining = round(remaining / monthly_burn, 1) if monthly_burn > 0 else None

    return {
        "exists": True,
        "id": reserve.id,
        "reserve_amount": reserve.reserve_amount,
        "drawn_to_date": drawn,
        "remaining": remaining,
        "pct_drawn": round(drawn / reserve.reserve_amount * 100, 1) if reserve.reserve_amount else 0,
        "interest_rate": reserve.interest_rate,
        "accrual_basis": reserve.accrual_basis,
        "notes": reserve.notes,
        "monthly_burn": round(monthly_burn, 2),
        "months_remaining": months_remaining,
        "draws": [{"id": d.id, "draw_date": d.draw_date, "amount": d.amount,
                   "period_start": d.period_start, "period_end": d.period_end,
                   "notes": d.notes} for d in draws],
    }


@router.post("/{project_id}/interest-reserve")
def upsert_interest_reserve(project_id: int, body: dict, db: Session = Depends(get_db),
                             user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    reserve = db.query(InterestReserve).filter(
        InterestReserve.project_id == project_id
    ).first()
    if not reserve:
        reserve = InterestReserve(
            org_id=proj.org_id, project_id=project_id,
            reserve_amount=body.get("reserve_amount", 0),
            interest_rate=body.get("interest_rate"),
            accrual_basis=body.get("accrual_basis", "actual/365"),
            notes=body.get("notes"),
            created_by=user.id,
        )
        db.add(reserve)
    else:
        for field in ["reserve_amount", "interest_rate", "accrual_basis", "notes"]:
            if field in body:
                setattr(reserve, field, body[field])
        reserve.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(reserve)
    return {"id": reserve.id, "ok": True}


@router.post("/{project_id}/interest-reserve/draws")
def add_reserve_draw(project_id: int, body: dict, db: Session = Depends(get_db),
                     user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    reserve = db.query(InterestReserve).filter(
        InterestReserve.project_id == project_id
    ).first()
    if not reserve:
        raise HTTPException(404, "Interest reserve not set up")
    d = InterestReserveDraw(
        reserve_id=reserve.id, org_id=proj.org_id, project_id=project_id,
        draw_date=body["draw_date"], amount=body["amount"],
        period_start=body.get("period_start"), period_end=body.get("period_end"),
        notes=body.get("notes"),
    )
    db.add(d)
    db.commit()
    return {"id": d.id, "ok": True}


@router.delete("/{project_id}/interest-reserve/draws/{draw_id}")
def delete_reserve_draw(project_id: int, draw_id: int, db: Session = Depends(get_db),
                        user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    d = db.query(InterestReserveDraw).filter(
        InterestReserveDraw.id == draw_id, InterestReserveDraw.project_id == project_id
    ).first()
    if d:
        db.delete(d)
        db.commit()
    return {"ok": True}


# ── WIP Report ─────────────────────────────────────────────────────────────────

@router.get("/{project_id}/wip-report")
def wip_report(project_id: int, db: Session = Depends(get_db),
               user=Depends(get_current_user)):
    """Work-in-Progress report: over/under billing, earned revenue, contract asset/liability."""
    proj = _get_project(project_id, user, db)

    # Total contract value (budget + approved change orders)
    approved_cos = db.query(ChangeOrder).filter(
        ChangeOrder.project_id == project_id,
        ChangeOrder.status == "approved"
    ).all()
    co_total = sum(c.amount for c in approved_cos)
    revised_contract = (proj.total_budget or 0) + co_total

    # Total invoiced (lender submitted)
    invoices = db.query(Invoice).filter(
        Invoice.project_id == project_id,
        Invoice.org_id == proj.org_id,
        Invoice.status == "processed",
    ).all()
    total_invoiced = sum(i.lender_submitted_amt or i.total_due or 0 for i in invoices)
    total_approved = sum(i.lender_approved_amt or 0 for i in invoices)
    total_paid = sum(i.amount_paid or 0 for i in invoices)

    # Percent complete (approved / contract)
    pct_complete = round(total_approved / revised_contract * 100, 1) if revised_contract else 0

    # Earned revenue = contract value × % complete
    earned_revenue = revised_contract * pct_complete / 100

    # Billing vs earned
    over_billing = max(0, total_invoiced - earned_revenue)   # billed more than earned
    under_billing = max(0, earned_revenue - total_invoiced)  # earned more than billed

    # Retainage / holdback
    total_holdback = sum(
        (i.lender_approved_amt or 0) * (i.holdback_pct or 10) / 100
        for i in invoices if (i.lender_approved_amt or 0) > 0
    )
    released_holdback = sum(
        (i.lender_approved_amt or 0) * (i.holdback_pct or 10) / 100
        for i in invoices if i.holdback_released
    )
    unreleased_holdback = total_holdback - released_holdback

    # Cost categories breakdown
    categories = db.query(CostCategory).filter(CostCategory.project_id == project_id).all()
    cat_breakdown = []
    for cat in categories:
        cat_invoiced = 0.0
        allocations = db.query(InvoiceAllocation).filter(
            InvoiceAllocation.category_id == cat.id
        ).all()
        for alloc in allocations:
            inv = db.query(Invoice).filter(Invoice.id == alloc.invoice_id).first()
            if inv and inv.status == "processed":
                cat_invoiced += alloc.amount or 0
        cat_co = sum(c.amount for c in approved_cos if c.category_id == cat.id)
        cat_budget = (cat.budget or 0) + cat_co
        cat_breakdown.append({
            "name": cat.name,
            "budget": cat_budget,
            "invoiced": cat_invoiced,
            "remaining": cat_budget - cat_invoiced,
            "pct": round(cat_invoiced / cat_budget * 100, 1) if cat_budget else 0,
        })

    return {
        "revised_contract": revised_contract,
        "original_contract": proj.total_budget or 0,
        "change_orders_approved": co_total,
        "pct_complete": pct_complete,
        "earned_revenue": round(earned_revenue, 2),
        "total_invoiced": round(total_invoiced, 2),
        "total_approved": round(total_approved, 2),
        "total_paid": round(total_paid, 2),
        "over_billing": round(over_billing, 2),
        "under_billing": round(under_billing, 2),
        "total_holdback": round(total_holdback, 2),
        "unreleased_holdback": round(unreleased_holdback, 2),
        "contract_asset": round(under_billing, 2),   # asset = we earned more than billed
        "contract_liability": round(over_billing, 2),  # liability = billed more than earned
        "categories": cat_breakdown,
    }


# ── Credit Committee Report ────────────────────────────────────────────────────

@router.get("/{project_id}/credit-committee-report")
def credit_committee_report(project_id: int, db: Session = Depends(get_db),
                             user=Depends(get_current_user)):
    """HTML credit committee reporting pack — lender board memo with RAG status."""
    proj = _get_project(project_id, user, db)
    wip = wip_report(project_id, db, user)

    covenants = db.query(LenderCovenant).filter(
        LenderCovenant.project_id == project_id
    ).all()
    breach_count = sum(1 for c in covenants if c.status == "breach")

    reserve = db.query(InterestReserve).filter(
        InterestReserve.project_id == project_id
    ).first()

    draws = db.query(Draw).filter(Draw.project_id == project_id).order_by(Draw.draw_number.desc()).all()

    # RAG scoring
    rag = "green"
    if breach_count > 0 or wip["over_billing"] > wip["revised_contract"] * 0.1:
        rag = "red"
    elif wip["pct_complete"] < 20 and len(draws) > 2:
        rag = "amber"

    rag_color = {"green": "#22c55e", "amber": "#f59e0b", "red": "#ef4444"}[rag]
    rag_label = {"green": "GREEN — ON TRACK", "amber": "AMBER — MONITOR", "red": "RED — ACTION REQUIRED"}[rag]

    reserve_html = ""
    if reserve:
        drawn = sum(d.amount for d in reserve.draws)
        remaining = reserve.reserve_amount - drawn
        pct = round(drawn / reserve.reserve_amount * 100, 1) if reserve.reserve_amount else 0
        reserve_html = f"""
        <tr><td>Interest Reserve Total</td><td>${reserve.reserve_amount:,.0f}</td></tr>
        <tr><td>Reserve Drawn to Date</td><td>${drawn:,.0f}</td></tr>
        <tr><td>Reserve Remaining</td><td>${remaining:,.0f} ({100-pct:.0f}% remaining)</td></tr>"""

    covenant_rows = "".join(f"""
        <tr>
          <td>{c.name}</td>
          <td>{c.covenant_type.upper()}</td>
          <td>{c.threshold_operator} {c.threshold_value or '—'}</td>
          <td>{c.current_value or '—'}</td>
          <td style="color:{'#ef4444' if c.status=='breach' else '#22c55e'};font-weight:bold">{c.status.upper()}</td>
        </tr>""" for c in covenants) or "<tr><td colspan='5' style='color:#94a3b8'>No covenants recorded</td></tr>"

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"/>
<title>Credit Committee Report — {proj.name}</title>
<style>
  body{{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:24px}}
  .card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;margin-bottom:16px}}
  h1{{color:#fff;margin-bottom:4px}}h2{{color:#94a3b8;font-size:14px;margin:0 0 16px}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{text-align:left;padding:8px 12px;background:#0f172a;color:#94a3b8;font-size:11px;text-transform:uppercase}}
  td{{padding:8px 12px;border-bottom:1px solid #334155}}
  .rag{{display:inline-block;padding:6px 16px;border-radius:6px;font-weight:bold;color:#fff;background:{rag_color}}}
  .metric{{display:inline-block;background:#0f172a;border-radius:8px;padding:12px 20px;margin:6px;text-align:center;min-width:140px}}
  .metric .val{{font-size:22px;font-weight:bold;color:#fff}}
  .metric .lbl{{font-size:11px;color:#94a3b8;margin-top:4px}}
</style>
</head><body>
<div class="card">
  <h1>{proj.name}</h1>
  <h2>Credit Committee Report — Generated {datetime.utcnow().strftime('%B %d, %Y')}</h2>
  <div class="rag">{rag_label}</div>
</div>

<div class="card">
  <h2>PROJECT OVERVIEW</h2>
  <div>
    <div class="metric"><div class="val">${wip['revised_contract']:,.0f}</div><div class="lbl">Revised Contract</div></div>
    <div class="metric"><div class="val">{wip['pct_complete']}%</div><div class="lbl">% Complete</div></div>
    <div class="metric"><div class="val">${wip['total_approved']:,.0f}</div><div class="lbl">Lender Approved</div></div>
    <div class="metric"><div class="val">${wip['unreleased_holdback']:,.0f}</div><div class="lbl">Holdback Held</div></div>
  </div>
</div>

<div class="card">
  <h2>WIP ANALYSIS</h2>
  <table>
    <tr><th>Item</th><th>Amount</th></tr>
    <tr><td>Earned Revenue</td><td>${wip['earned_revenue']:,.0f}</td></tr>
    <tr><td>Total Invoiced</td><td>${wip['total_invoiced']:,.0f}</td></tr>
    <tr><td style="color:{'#ef4444' if wip['over_billing'] > 0 else '#94a3b8'}">Over-Billing (Contract Liability)</td><td>${wip['over_billing']:,.0f}</td></tr>
    <tr><td style="color:{'#22c55e' if wip['under_billing'] > 0 else '#94a3b8'}">Under-Billing (Contract Asset)</td><td>${wip['under_billing']:,.0f}</td></tr>
    {reserve_html}
  </table>
</div>

<div class="card">
  <h2>COVENANT COMPLIANCE ({len(covenants)} covenants, {breach_count} breach{'es' if breach_count!=1 else ''})</h2>
  <table>
    <tr><th>Covenant</th><th>Type</th><th>Threshold</th><th>Current</th><th>Status</th></tr>
    {covenant_rows}
  </table>
</div>

<div class="card">
  <h2>DRAW HISTORY ({len(draws)} draws)</h2>
  <table>
    <tr><th>Draw #</th><th>Date</th><th>Status</th></tr>
    {"".join(f'<tr><td>Draw #{d.draw_number}</td><td>{d.submission_date or "—"}</td><td>{d.status.upper()}</td></tr>' for d in draws) or '<tr><td colspan="3" style="color:#94a3b8">No draws</td></tr>'}
  </table>
</div>
<div style="text-align:center;color:#475569;font-size:11px;margin-top:24px">
  Powered by Finel AI Projects — Confidential — For Lender Use Only
</div>
</body></html>"""

    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)
