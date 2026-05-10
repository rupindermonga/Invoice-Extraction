"""
Enhanced lender/owner workflows:
- Funding conditions per draw
- Draw certificates (inspector/consultant)
- Statutory declarations per draw
- Owner portal tokens + view
- CCDC progress claim template
"""
import os, secrets, uuid
from datetime import datetime as dt
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    User, Project, Draw, Invoice, Organization, CostCategory, InvoiceAllocation,
    FundingCondition, DrawCertificate, StatutoryDeclaration, OwnerToken,
    LenderToken, Claim,
)
from ..dependencies import get_current_user, get_current_org
from .audit import log as audit_log

router = APIRouter(prefix="/api/project", tags=["lender-plus"])

UPLOAD_DIR = os.getenv("UPLOAD_FOLDER", "./uploads")
CERT_DIR   = os.path.join(UPLOAD_DIR, "certificates")
os.makedirs(CERT_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
#  FUNDING CONDITIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/draws/{draw_id}/conditions")
def list_conditions(
    draw_id: int,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    org, _ = org_ctx
    conds = db.query(FundingCondition).filter(
        FundingCondition.draw_id == draw_id,
        FundingCondition.org_id == org.id,
    ).order_by(FundingCondition.created_at).all()
    return [_cond_out(c) for c in conds]


def _cond_out(c: FundingCondition) -> dict:
    return {
        "id": c.id, "description": c.description, "condition_type": c.condition_type,
        "status": c.status, "required_by": c.required_by,
        "satisfied_date": c.satisfied_date, "notes": c.notes,
        "created_at": str(c.created_at),
    }


@router.post("/draws/{draw_id}/conditions")
def create_condition(
    draw_id: int,
    body: dict,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org, _ = org_ctx
    draw = db.query(Draw).filter(Draw.id == draw_id).first()
    if not draw:
        raise HTTPException(404, "Draw not found")
    c = FundingCondition(
        org_id=org.id, project_id=draw.project_id, draw_id=draw_id,
        description=(body.get("description") or "").strip(),
        condition_type=body.get("condition_type", "document"),
        status=body.get("status", "open"),
        required_by=body.get("required_by"),
        notes=body.get("notes"),
        created_by=current_user.id,
    )
    db.add(c); db.commit(); db.refresh(c)
    audit_log(db, org.id, current_user, "create_funding_condition", "draw", draw_id,
              detail=f"Added condition: {c.description}")
    return _cond_out(c)


@router.put("/conditions/{cond_id}")
def update_condition(
    cond_id: int,
    body: dict,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org, _ = org_ctx
    c = db.query(FundingCondition).filter(
        FundingCondition.id == cond_id, FundingCondition.org_id == org.id
    ).first()
    if not c:
        raise HTTPException(404)
    for k in ("description","condition_type","status","required_by","satisfied_date","notes"):
        if k in body:
            setattr(c, k, body[k])
    if body.get("status") == "satisfied" and not c.satisfied_date:
        c.satisfied_date = dt.utcnow().strftime("%Y-%m-%d")
    db.commit()
    return _cond_out(c)


@router.delete("/conditions/{cond_id}")
def delete_condition(
    cond_id: int,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    org, _ = org_ctx
    c = db.query(FundingCondition).filter(
        FundingCondition.id == cond_id, FundingCondition.org_id == org.id
    ).first()
    if not c:
        raise HTTPException(404)
    db.delete(c); db.commit()
    return {"message": "Deleted"}


# ══════════════════════════════════════════════════════════════════════════════
#  DRAW CERTIFICATES (Inspector / Consultant)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/draws/{draw_id}/certificates")
def list_certificates(
    draw_id: int,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    org, _ = org_ctx
    certs = db.query(DrawCertificate).filter(
        DrawCertificate.draw_id == draw_id,
        DrawCertificate.org_id == org.id,
    ).order_by(DrawCertificate.created_at).all()
    return [_cert_out(c) for c in certs]


def _cert_out(c: DrawCertificate) -> dict:
    return {
        "id": c.id, "cert_type": c.cert_type, "certifier_name": c.certifier_name,
        "certifier_firm": c.certifier_firm, "cert_date": c.cert_date,
        "amount_certified": c.amount_certified, "status": c.status,
        "has_file": bool(c.file_path), "original_filename": c.original_filename,
        "notes": c.notes, "created_at": str(c.created_at),
    }


@router.post("/draws/{draw_id}/certificates")
def create_certificate(
    draw_id: int,
    body: dict,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org, _ = org_ctx
    draw = db.query(Draw).filter(Draw.id == draw_id).first()
    if not draw:
        raise HTTPException(404, "Draw not found")
    cert = DrawCertificate(
        org_id=org.id, project_id=draw.project_id, draw_id=draw_id,
        cert_type=body.get("cert_type", "progress"),
        certifier_name=body.get("certifier_name"),
        certifier_firm=body.get("certifier_firm"),
        cert_date=body.get("cert_date"),
        amount_certified=body.get("amount_certified"),
        status=body.get("status", "pending"),
        notes=body.get("notes"),
        created_by=current_user.id,
    )
    db.add(cert); db.commit(); db.refresh(cert)
    audit_log(db, org.id, current_user, "add_certificate", "draw", draw_id,
              detail=f"{cert.cert_type} cert by {cert.certifier_name or 'unknown'}")
    return _cert_out(cert)


@router.put("/certificates/{cert_id}")
def update_certificate(
    cert_id: int,
    body: dict,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    org, _ = org_ctx
    cert = db.query(DrawCertificate).filter(
        DrawCertificate.id == cert_id, DrawCertificate.org_id == org.id
    ).first()
    if not cert:
        raise HTTPException(404)
    for k in ("cert_type","certifier_name","certifier_firm","cert_date","amount_certified","status","notes"):
        if k in body:
            setattr(cert, k, body[k])
    db.commit()
    return _cert_out(cert)


@router.post("/certificates/{cert_id}/upload")
async def upload_certificate_file(
    cert_id: int,
    file: UploadFile = File(...),
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    org, _ = org_ctx
    cert = db.query(DrawCertificate).filter(
        DrawCertificate.id == cert_id, DrawCertificate.org_id == org.id
    ).first()
    if not cert:
        raise HTTPException(404)
    ext = os.path.splitext(file.filename or "cert.pdf")[1].lower()
    fname = f"cert_{uuid.uuid4().hex}{ext}"
    fpath = os.path.join(CERT_DIR, fname)
    content = await file.read()
    with open(fpath, "wb") as f:
        f.write(content)
    cert.file_path = fpath
    cert.original_filename = file.filename
    cert.status = "submitted"
    db.commit()
    return {"message": "File uploaded", "filename": file.filename}


@router.delete("/certificates/{cert_id}")
def delete_certificate(
    cert_id: int,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    org, _ = org_ctx
    cert = db.query(DrawCertificate).filter(
        DrawCertificate.id == cert_id, DrawCertificate.org_id == org.id
    ).first()
    if not cert:
        raise HTTPException(404)
    if cert.file_path and os.path.isfile(cert.file_path):
        try:
            os.remove(cert.file_path)
        except Exception:
            pass
    db.delete(cert); db.commit()
    return {"message": "Deleted"}


# ══════════════════════════════════════════════════════════════════════════════
#  STATUTORY DECLARATIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/draws/{draw_id}/statutory-declarations")
def list_statutory_declarations(
    draw_id: int,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    org, _ = org_ctx
    decls = db.query(StatutoryDeclaration).filter(
        StatutoryDeclaration.draw_id == draw_id,
        StatutoryDeclaration.org_id == org.id,
    ).all()
    return [_decl_out(d) for d in decls]


def _decl_out(d: StatutoryDeclaration) -> dict:
    return {
        "id": d.id, "vendor_name": d.vendor_name, "declaration_date": d.declaration_date,
        "period_end": d.period_end, "amount": d.amount, "status": d.status,
        "has_file": bool(d.file_path), "created_at": str(d.created_at),
    }


@router.post("/draws/{draw_id}/statutory-declarations")
def create_statutory_declaration(
    draw_id: int,
    body: dict,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    org, _ = org_ctx
    draw = db.query(Draw).filter(Draw.id == draw_id).first()
    if not draw:
        raise HTTPException(404)
    d = StatutoryDeclaration(
        org_id=org.id, project_id=draw.project_id, draw_id=draw_id,
        vendor_name=(body.get("vendor_name") or "").strip(),
        declaration_date=body.get("declaration_date"),
        period_end=body.get("period_end"),
        amount=body.get("amount"),
        status=body.get("status", "required"),
        vendor_id=body.get("vendor_id"),
    )
    db.add(d); db.commit(); db.refresh(d)
    return _decl_out(d)


@router.put("/statutory-declarations/{decl_id}")
def update_statutory_declaration(
    decl_id: int,
    body: dict,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    org, _ = org_ctx
    d = db.query(StatutoryDeclaration).filter(
        StatutoryDeclaration.id == decl_id, StatutoryDeclaration.org_id == org.id
    ).first()
    if not d:
        raise HTTPException(404)
    for k in ("vendor_name","declaration_date","period_end","amount","status"):
        if k in body:
            setattr(d, k, body[k])
    db.commit()
    return _decl_out(d)


# ══════════════════════════════════════════════════════════════════════════════
#  DRAW READINESS SUMMARY (for lender portal)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/draws/{draw_id}/readiness")
def draw_readiness(
    draw_id: int,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full draw readiness check: conditions, certs, stat decls, invoice approvals."""
    org, _ = org_ctx
    draw = db.query(Draw).filter(Draw.id == draw_id).first()
    if not draw:
        raise HTTPException(404)

    invs = db.query(Invoice).filter(
        Invoice.draw_id == draw_id, Invoice.user_id == current_user.id
    ).all()

    conds = db.query(FundingCondition).filter(
        FundingCondition.draw_id == draw_id
    ).all()
    certs = db.query(DrawCertificate).filter(
        DrawCertificate.draw_id == draw_id
    ).all()
    decls = db.query(StatutoryDeclaration).filter(
        StatutoryDeclaration.draw_id == draw_id
    ).all()

    open_conds = [c for c in conds if c.status == "open"]
    pending_certs = [c for c in certs if c.status in ("pending",)]
    missing_decls = [d for d in decls if d.status == "required"]
    unapproved_invs = [i for i in invs if i.approval_status not in ("approved",)]

    total_submitted = sum(i.lender_submitted_amt or 0 for i in invs)
    total_approved  = sum(i.lender_approved_amt  or 0 for i in invs)
    total_holdback  = sum((i.lender_submitted_amt or 0) * (i.holdback_pct or 0) / 100 for i in invs)

    is_ready = (
        len(open_conds) == 0 and
        len(pending_certs) == 0 and
        len(missing_decls) == 0 and
        len(unapproved_invs) == 0
    )

    blockers = []
    if open_conds:
        blockers.append(f"{len(open_conds)} funding condition(s) open")
    if pending_certs:
        blockers.append(f"{len(pending_certs)} consultant certificate(s) pending")
    if missing_decls:
        blockers.append(f"{len(missing_decls)} statutory declaration(s) missing")
    if unapproved_invs:
        blockers.append(f"{len(unapproved_invs)} invoice(s) not approved")

    return {
        "draw_id": draw_id,
        "draw_number": draw.draw_number,
        "is_ready": is_ready,
        "blockers": blockers,
        "invoice_count": len(invs),
        "total_submitted": round(total_submitted, 2),
        "total_approved": round(total_approved, 2),
        "total_holdback": round(total_holdback, 2),
        "net_claim": round(total_submitted - total_holdback, 2),
        "conditions": {"total": len(conds), "open": len(open_conds), "satisfied": len(conds) - len(open_conds)},
        "certificates": {"total": len(certs), "pending": len(pending_certs), "accepted": len([c for c in certs if c.status == "accepted"])},
        "statutory_declarations": {"total": len(decls), "missing": len(missing_decls), "received": len([d for d in decls if d.status == "received"])},
    }


# ══════════════════════════════════════════════════════════════════════════════
#  OWNER TOKENS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/owner-tokens")
def list_owner_tokens(
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org, _ = org_ctx
    tokens = db.query(OwnerToken).filter(
        OwnerToken.org_id == org.id
    ).order_by(OwnerToken.created_at.desc()).all()
    return [_owner_token_out(t) for t in tokens]


def _owner_token_out(t: OwnerToken) -> dict:
    return {
        "id": t.id, "project_id": t.project_id, "label": t.label,
        "token": t.token, "is_active": t.is_active,
        "expires_at": t.expires_at, "created_at": str(t.created_at),
        "url": f"/owner/{t.token}",
    }


@router.post("/owner-tokens")
def create_owner_token(
    body: dict,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org, _ = org_ctx
    project_id = body.get("project_id")
    if not project_id:
        raise HTTPException(400, "project_id required")
    t = OwnerToken(
        project_id=project_id, org_id=org.id,
        token=secrets.token_urlsafe(24),
        label=body.get("label", "Owner Portal"),
        expires_at=body.get("expires_at"),
        created_by=current_user.id,
    )
    db.add(t); db.commit(); db.refresh(t)
    audit_log(db, org.id, current_user, "create_owner_token", "project", project_id,
              detail=f"Created owner portal '{t.label}'")
    return _owner_token_out(t)


@router.put("/owner-tokens/{token_id}/toggle")
def toggle_owner_token(
    token_id: int,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    org, _ = org_ctx
    t = db.query(OwnerToken).filter(
        OwnerToken.id == token_id, OwnerToken.org_id == org.id
    ).first()
    if not t:
        raise HTTPException(404)
    t.is_active = not t.is_active
    db.commit()
    return {"id": t.id, "is_active": t.is_active}


@router.delete("/owner-tokens/{token_id}")
def delete_owner_token(
    token_id: int,
    org_ctx: Tuple = Depends(get_current_org),
    db: Session = Depends(get_db),
):
    org, _ = org_ctx
    t = db.query(OwnerToken).filter(
        OwnerToken.id == token_id, OwnerToken.org_id == org.id
    ).first()
    if not t:
        raise HTTPException(404)
    db.delete(t); db.commit()
    return {"message": "Deleted"}


# ── Public owner portal data endpoint ─────────────────────────────────────────

_owner_router = APIRouter(prefix="/owner", tags=["owner-portal"])


@_owner_router.get("/{token}")
def owner_portal_data(token: str, db: Session = Depends(get_db)):
    """Public endpoint: owner views project overview via token (no login required)."""
    rec = db.query(OwnerToken).filter(
        OwnerToken.token == token, OwnerToken.is_active == True
    ).first()
    if not rec:
        raise HTTPException(404, "Owner portal link not found or has been deactivated.")
    if rec.expires_at and rec.expires_at < dt.utcnow().strftime("%Y-%m-%d"):
        raise HTTPException(403, "This portal link has expired.")

    proj = db.query(Project).filter(Project.id == rec.project_id).first()
    if not proj:
        raise HTTPException(404)

    # Budget overview
    draws = db.query(Draw).filter(Draw.project_id == proj.id).all()
    invs  = db.query(Invoice).filter(Invoice.project_id == proj.id, Invoice.status == "processed").all()
    total_invoiced  = sum(i.total_due or 0 for i in invs)
    total_submitted = sum(i.lender_submitted_amt or 0 for i in invs)
    total_approved  = sum(i.lender_approved_amt  or 0 for i in invs)
    total_holdback  = sum((i.lender_submitted_amt or 0) * (i.holdback_pct or 0) / 100 for i in invs)

    # Draw summaries
    draw_summaries = []
    for draw in draws:
        d_invs = [i for i in invs if i.draw_id == draw.id]
        d_sub = sum(i.lender_submitted_amt or 0 for i in d_invs)
        d_app = sum(i.lender_approved_amt  or 0 for i in d_invs)
        d_hb  = sum((i.lender_submitted_amt or 0) * (i.holdback_pct or 0) / 100 for i in d_invs)
        readiness = db.query(FundingCondition).filter(
            FundingCondition.draw_id == draw.id,
            FundingCondition.status == "open"
        ).count()
        draw_summaries.append({
            "draw_number": draw.draw_number,
            "submission_date": draw.submission_date,
            "status": draw.status,
            "invoice_count": len(d_invs),
            "submitted": round(d_sub, 2),
            "approved": round(d_app, 2),
            "holdback": round(d_hb, 2),
            "net_claim": round(d_sub - d_hb, 2),
            "open_conditions": readiness,
        })

    # Category overview
    categories = db.query(CostCategory).filter(CostCategory.project_id == proj.id).all()
    cat_data = []
    for cat in categories:
        spent = db.query(func.coalesce(func.sum(InvoiceAllocation.amount), 0.0)).filter(
            InvoiceAllocation.category_id == cat.id
        ).scalar() or 0
        cat_data.append({
            "name": cat.name,
            "budget": round(cat.budget or 0, 2),
            "spent": round(spent, 2),
            "pct": round(spent / cat.budget * 100, 1) if cat.budget else 0,
        })

    return {
        "project_name": proj.name,
        "project_code": proj.code,
        "address": proj.address,
        "label": rec.label,
        "generated": dt.utcnow().strftime("%B %d, %Y"),
        "budget": {"total": round(proj.total_budget or 0, 2), "lender": round(proj.lender_budget or 0, 2)},
        "totals": {
            "invoiced": round(total_invoiced, 2),
            "submitted": round(total_submitted, 2),
            "approved": round(total_approved, 2),
            "holdback": round(total_holdback, 2),
            "net_released": round(total_approved - total_holdback, 2),
            "spend_pct": round(total_invoiced / proj.total_budget * 100, 1) if proj.total_budget else 0,
        },
        "draws": sorted(draw_summaries, key=lambda d: d["draw_number"]),
        "categories": cat_data,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  CCDC PROGRESS CLAIM TEMPLATE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/draws/{draw_id}/ccdc-claim")
def ccdc_progress_claim(
    draw_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate a CCDC-style Progress Payment Certificate / Claim form.
    Print-ready HTML — open in new tab and Ctrl+P to save as PDF.
    """
    draw = (
        db.query(Draw)
        .join(Project, Project.id == Draw.project_id)
        .filter(Draw.id == draw_id, Project.user_id == current_user.id)
        .first()
    )
    if not draw:
        raise HTTPException(404, "Draw not found")

    proj = db.query(Project).filter(Project.id == draw.project_id).first()
    org  = db.query(Organization).filter(Organization.id == proj.org_id).first() if proj and proj.org_id else None

    invs = (
        db.query(Invoice)
        .filter(Invoice.draw_id == draw_id, Invoice.user_id == current_user.id, Invoice.status == "processed")
        .order_by(Invoice.vendor_name)
        .all()
    )

    total_sub   = sum(i.lender_submitted_amt or 0 for i in invs)
    total_hold  = sum((i.lender_submitted_amt or 0) * (i.holdback_pct or 0) / 100 for i in invs)
    total_net   = total_sub - total_hold
    total_app   = sum(i.lender_approved_amt or 0 for i in invs)
    gen_date    = dt.utcnow().strftime("%B %d, %Y")

    # Previous draws total
    prev_draws = db.query(Draw).filter(
        Draw.project_id == draw.project_id,
        Draw.draw_number < draw.draw_number,
    ).all()
    prev_total = 0.0
    for pd in prev_draws:
        prev_total += sum(
            (i.lender_approved_amt or 0)
            for i in db.query(Invoice).filter(Invoice.draw_id == pd.id).all()
        )

    cumulative_claimed = prev_total + total_sub
    budget = proj.lender_budget or proj.total_budget or 0
    pct_complete = round(cumulative_claimed / budget * 100, 1) if budget else 0

    categories = db.query(CostCategory).filter(CostCategory.project_id == proj.id).all()
    cat_rows = ""
    for cat in categories:
        cat_budget = cat.lender_budget or cat.budget or 0
        cat_spent = db.query(func.coalesce(func.sum(InvoiceAllocation.amount), 0.0)).filter(
            InvoiceAllocation.category_id == cat.id
        ).scalar() or 0
        cat_rows += f"""
        <tr>
          <td style="padding:7px 10px;border:1px solid #ddd;">{cat.name}</td>
          <td style="padding:7px 10px;border:1px solid #ddd;text-align:right;">${cat_budget:,.2f}</td>
          <td style="padding:7px 10px;border:1px solid #ddd;text-align:right;">${cat_spent:,.2f}</td>
          <td style="padding:7px 10px;border:1px solid #ddd;text-align:right;">{round(cat_spent/cat_budget*100,1) if cat_budget else 0}%</td>
          <td style="padding:7px 10px;border:1px solid #ddd;text-align:right;">${max(0,cat_budget-cat_spent):,.2f}</td>
        </tr>"""

    org_name  = org.name if org else ""
    proj_name = proj.name if proj else "Project"
    proj_addr = proj.address or ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>CCDC Progress Claim — Draw #{draw.draw_number}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:Arial,sans-serif;font-size:12px;color:#1a1a1a;background:#fff;}}
@media print{{body{{-webkit-print-color-adjust:exact;print-color-adjust:exact;}} .noprint{{display:none;}}}}
.wrap{{max-width:900px;margin:0 auto;padding:32px 28px;}}
.print-btn{{position:fixed;bottom:20px;right:20px;background:#005366;color:#fff;border:none;padding:10px 20px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;}}
.header{{border:2px solid #005366;padding:0;margin-bottom:16px;}}
.header-top{{background:#005366;color:#fff;padding:10px 16px;display:flex;justify-content:space-between;align-items:center;}}
.header-top h1{{font-size:16px;font-weight:700;letter-spacing:.04em;}}
.header-top .sub{{font-size:11px;opacity:.8;}}
.info-grid{{display:grid;grid-template-columns:1fr 1fr;gap:0;border-top:0;}}
.info-cell{{padding:8px 14px;border-right:1px solid #ddd;border-bottom:1px solid #ddd;}}
.info-cell:nth-child(2n){{border-right:none;}}
.info-cell label{{font-size:9px;font-weight:700;text-transform:uppercase;color:#666;letter-spacing:.06em;display:block;margin-bottom:2px;}}
.info-cell value{{font-size:12px;font-weight:600;}}
table{{width:100%;border-collapse:collapse;margin-bottom:14px;font-size:11px;}}
thead tr{{background:#1e293b;color:#fff;}}
th{{padding:7px 10px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;}}
.summary-box{{background:#f0fdf4;border:2px solid #16a34a;border-radius:8px;padding:16px;margin-bottom:16px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;}}
.sum-item label{{font-size:9px;text-transform:uppercase;font-weight:700;color:#16a34a;letter-spacing:.05em;display:block;margin-bottom:2px;}}
.sum-item value{{font-size:16px;font-weight:700;color:#15803d;}}
.sig-grid{{display:grid;grid-template-columns:1fr 1fr;gap:32px;margin-top:28px;}}
.sig{{border-top:1px solid #1e293b;padding-top:6px;font-size:10px;color:#555;}}
.sig strong{{display:block;font-size:11px;margin-bottom:18px;color:#1e293b;}}
section-title{{display:block;font-size:11px;font-weight:700;color:#005366;text-transform:uppercase;letter-spacing:.06em;margin:14px 0 6px;border-bottom:1px solid #005366;padding-bottom:3px;}}
.notice{{background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;padding:8px 12px;font-size:10px;color:#92400e;margin-bottom:14px;}}
</style>
</head>
<body>
<button class="print-btn noprint" onclick="window.print()">🖨 Print / PDF</button>
<div class="wrap">

  <div class="header">
    <div class="header-top">
      <div>
        <h1>PROGRESS PAYMENT CERTIFICATE</h1>
        <div class="sub">Based on CCDC 2 Standard Form of Contract</div>
      </div>
      <div style="text-align:right;">
        <div style="font-size:18px;font-weight:700;">Draw #{draw.draw_number}</div>
        <div style="font-size:10px;opacity:.8;">{gen_date}</div>
      </div>
    </div>
    <div class="info-grid">
      <div class="info-cell"><label>Project</label><value>{proj_name}</value></div>
      <div class="info-cell"><label>Project Code</label><value>{proj.code or "—"}</value></div>
      <div class="info-cell"><label>Location / Address</label><value>{proj_addr or "—"}</value></div>
      <div class="info-cell"><label>Owner / Organization</label><value>{org_name or "—"}</value></div>
      <div class="info-cell"><label>Submission Date</label><value>{draw.submission_date or "—"}</value></div>
      <div class="info-cell"><label>Contract Value</label><value>${budget:,.2f} CAD</value></div>
    </div>
  </div>

  <div class="notice">
    This certificate is issued in accordance with the CCDC 2 Stipulated Price Contract. Payment is due within the prompt-payment period applicable under provincial legislation.
  </div>

  <span class="section-title">Claim Summary</span>
  <table>
    <thead><tr>
      <th>Description</th><th style="text-align:right;">Previous Draws</th>
      <th style="text-align:right;">This Draw</th><th style="text-align:right;">Cumulative</th>
      <th style="text-align:right;">Holdback ({round(total_hold/total_sub*100,1) if total_sub else 0}%)</th>
      <th style="text-align:right;">Net Claim</th>
    </tr></thead>
    <tbody>
      <tr>
        <td style="padding:8px 10px;border:1px solid #ddd;">Work completed to date</td>
        <td style="padding:8px 10px;border:1px solid #ddd;text-align:right;">${prev_total:,.2f}</td>
        <td style="padding:8px 10px;border:1px solid #ddd;text-align:right;">${total_sub:,.2f}</td>
        <td style="padding:8px 10px;border:1px solid #ddd;text-align:right;">${cumulative_claimed:,.2f}</td>
        <td style="padding:8px 10px;border:1px solid #ddd;text-align:right;">(${total_hold:,.2f})</td>
        <td style="padding:8px 10px;border:1px solid #ddd;text-align:right;font-weight:700;">${total_net:,.2f}</td>
      </tr>
    </tbody>
  </table>

  <div class="summary-box">
    <div class="sum-item"><label>Amount Claimed This Draw</label><value>${total_sub:,.2f} CAD</value></div>
    <div class="sum-item"><label>Holdback Retained</label><value>(${total_hold:,.2f} CAD)</value></div>
    <div class="sum-item"><label>Net Amount Due</label><value>${total_net:,.2f} CAD</value></div>
  </div>

  <span class="section-title">Schedule of Values by Cost Category</span>
  <table>
    <thead><tr>
      <th>Category</th><th style="text-align:right;">Contract Value</th>
      <th style="text-align:right;">Claimed to Date</th>
      <th style="text-align:right;">% Complete</th>
      <th style="text-align:right;">Balance to Complete</th>
    </tr></thead>
    <tbody>{cat_rows}</tbody>
  </table>

  <span class="section-title">Contractor Certification</span>
  <p style="font-size:11px;color:#444;margin-bottom:16px;line-height:1.6;">
    The undersigned contractor hereby certifies that the work described herein has been executed in accordance with the Contract Documents,
    that all subcontractors and suppliers have been paid for previous draws except as noted,
    and that this claim accurately represents the value of work completed as of the claim date.
  </p>

  <div class="sig-grid">
    <div class="sig">
      <strong>Contractor (General Contractor)</strong>
      Company: ________________________________<br/><br/>
      Name: ________________________________<br/><br/>
      Signature: ________________________________<br/><br/>
      Date: ________________________________
    </div>
    <div class="sig">
      <strong>Certified by (Consultant / Owner's Representative)</strong>
      Name: ________________________________<br/><br/>
      Firm: ________________________________<br/><br/>
      Signature: ________________________________<br/><br/>
      Date: ________________________________<br/><br/>
      Amount Certified: $____________________
    </div>
  </div>

  <div style="margin-top:28px;text-align:center;font-size:9px;color:#999;border-top:1px solid #eee;padding-top:12px;">
    Generated by Finel AI Projects · projects.finel.ai · {gen_date} · Based on CCDC 2 Standard Form of Contract
  </div>
</div>
</body>
</html>"""

    return HTMLResponse(content=html, media_type="text/html")
