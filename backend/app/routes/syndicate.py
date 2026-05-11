"""Syndicated Loan Servicing — multi-lender construction facility management."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..dependencies import get_current_user, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import LoanSyndicate, SyndicateParticipant, Project, Draw, Invoice

router = APIRouter(prefix="/api/project", tags=["syndicate"])


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _proj(project_id, user, db):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p: raise HTTPException(404)
    require_org_member(db, p.org_id, user.id, FINANCE_READ_ROLES)
    return p


# ── Syndicates ─────────────────────────────────────────────────────────────────

@router.get("/{project_id}/syndicates")
def list_syndicates(project_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    _proj(project_id, user, db)
    synds = db.query(LoanSyndicate).filter(LoanSyndicate.project_id == project_id).all()
    result = []
    for s in synds:
        total_pct = sum(p.participation_pct for p in s.participants)
        result.append({
            "id": s.id, "facility_name": s.facility_name,
            "total_commitment": s.total_commitment, "currency": s.currency,
            "lead_lender": s.lead_lender, "closing_date": s.closing_date,
            "maturity_date": s.maturity_date, "interest_rate": s.interest_rate,
            "notes": s.notes, "participant_count": len(s.participants),
            "total_pct_allocated": round(total_pct, 2),
            "unallocated_pct": round(100 - total_pct, 2),
            "participants": [_part_out(p, s.total_commitment) for p in s.participants],
            "created_at": s.created_at.isoformat(),
        })
    return result


def _part_out(p, total_commitment):
    commitment = p.commitment_amount or (total_commitment * p.participation_pct / 100)
    return {
        "id": p.id, "lender_name": p.lender_name,
        "participation_pct": p.participation_pct,
        "commitment_amount": round(commitment, 2),
        "contact_name": p.contact_name, "contact_email": p.contact_email,
        "reporting_email": p.reporting_email, "notes": p.notes,
    }


@router.post("/{project_id}/syndicates")
def create_syndicate(project_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    s = LoanSyndicate(
        org_id=p.org_id, project_id=project_id,
        facility_name=body["facility_name"],
        total_commitment=body["total_commitment"],
        currency=body.get("currency", "CAD"),
        lead_lender=body.get("lead_lender"),
        closing_date=body.get("closing_date"),
        maturity_date=body.get("maturity_date"),
        interest_rate=body.get("interest_rate"),
        notes=body.get("notes"),
        created_by=user.id,
    )
    db.add(s); db.commit(); db.refresh(s)
    return {"id": s.id, "ok": True}


@router.put("/{project_id}/syndicates/{syn_id}")
def update_syndicate(project_id: int, syn_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    s = db.query(LoanSyndicate).filter(LoanSyndicate.id == syn_id, LoanSyndicate.project_id == project_id).first()
    if not s: raise HTTPException(404)
    for f in ["facility_name","total_commitment","currency","lead_lender","closing_date","maturity_date","interest_rate","notes"]:
        if f in body: setattr(s, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/syndicates/{syn_id}")
def delete_syndicate(project_id: int, syn_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    s = db.query(LoanSyndicate).filter(LoanSyndicate.id == syn_id, LoanSyndicate.project_id == project_id).first()
    if s: db.delete(s); db.commit()
    return {"ok": True}


# ── Participants ───────────────────────────────────────────────────────────────

@router.post("/{project_id}/syndicates/{syn_id}/participants")
def add_participant(project_id: int, syn_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    s = db.query(LoanSyndicate).filter(LoanSyndicate.id == syn_id, LoanSyndicate.project_id == project_id).first()
    if not s: raise HTTPException(404)
    part = SyndicateParticipant(
        syndicate_id=syn_id, org_id=p.org_id,
        lender_name=body["lender_name"],
        participation_pct=body["participation_pct"],
        commitment_amount=body.get("commitment_amount"),
        contact_name=body.get("contact_name"),
        contact_email=body.get("contact_email"),
        reporting_email=body.get("reporting_email"),
        notes=body.get("notes"),
    )
    db.add(part); db.commit(); db.refresh(part)
    return {"id": part.id, "ok": True}


@router.put("/{project_id}/syndicates/{syn_id}/participants/{part_id}")
def update_participant(project_id: int, syn_id: int, part_id: int, body: dict,
                       db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    part = db.query(SyndicateParticipant).filter(
        SyndicateParticipant.id == part_id, SyndicateParticipant.syndicate_id == syn_id
    ).first()
    if not part: raise HTTPException(404)
    for f in ["lender_name","participation_pct","commitment_amount","contact_name","contact_email","reporting_email","notes"]:
        if f in body: setattr(part, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/syndicates/{syn_id}/participants/{part_id}")
def delete_participant(project_id: int, syn_id: int, part_id: int,
                       db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    part = db.query(SyndicateParticipant).filter(
        SyndicateParticipant.id == part_id, SyndicateParticipant.syndicate_id == syn_id
    ).first()
    if part: db.delete(part); db.commit()
    return {"ok": True}


# ── Participant Draw Report ────────────────────────────────────────────────────

@router.get("/{project_id}/syndicates/{syn_id}/draw-report")
def participant_draw_report(project_id: int, syn_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    """Per-lender draw allocation — each participant's share of every draw."""
    _proj(project_id, user, db)
    s = db.query(LoanSyndicate).filter(LoanSyndicate.id == syn_id, LoanSyndicate.project_id == project_id).first()
    if not s: raise HTTPException(404)
    draws = db.query(Draw).filter(Draw.project_id == project_id).order_by(Draw.draw_number).all()
    report = []
    for draw in draws:
        invoices = db.query(Invoice).filter(Invoice.draw_id == draw.id).all()
        draw_approved = sum(i.lender_approved_amt or 0 for i in invoices)
        draw_submitted = sum(i.lender_submitted_amt or i.total_due or 0 for i in invoices)
        participant_shares = []
        for part in s.participants:
            pct = part.participation_pct / 100
            commitment = part.commitment_amount or (s.total_commitment * pct)
            participant_shares.append({
                "lender": part.lender_name,
                "pct": part.participation_pct,
                "submitted_share": round(draw_submitted * pct, 2),
                "approved_share": round(draw_approved * pct, 2),
                "commitment": round(commitment, 2),
            })
        report.append({
            "draw_number": draw.draw_number,
            "draw_id": draw.id,
            "status": draw.status,
            "submission_date": draw.submission_date,
            "total_submitted": round(draw_submitted, 2),
            "total_approved": round(draw_approved, 2),
            "participant_shares": participant_shares,
        })
    # Cumulative totals
    total_approved = sum(d["total_approved"] for d in report)
    cumulative = []
    for part in s.participants:
        pct = part.participation_pct / 100
        cumulative.append({
            "lender": part.lender_name,
            "pct": part.participation_pct,
            "total_approved_share": round(total_approved * pct, 2),
            "commitment": round(part.commitment_amount or s.total_commitment * pct, 2),
            "utilization_pct": round(total_approved * pct / (part.commitment_amount or s.total_commitment * pct) * 100, 1)
                if (part.commitment_amount or s.total_commitment * pct) > 0 else 0,
        })
    return {
        "facility": {"name": s.facility_name, "total_commitment": s.total_commitment, "lead_lender": s.lead_lender},
        "draws": report,
        "cumulative": cumulative,
        "total_approved": round(total_approved, 2),
    }
