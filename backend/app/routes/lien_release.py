"""Lien Release Workflow + Vendor Scorecard."""
from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..dependencies import get_current_user, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import LienRelease, VendorScore, Project, OrgVendor
from ..routes.compliance import PROVINCE_RULES

router = APIRouter(prefix="/api/project", tags=["lien-release"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_project(project_id: int, user, db: Session) -> Project:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if not proj: raise HTTPException(404)
    require_org_member(db, proj.org_id, user.id, FINANCE_READ_ROLES)
    return proj


# ── Lien Releases ──────────────────────────────────────────────────────────────

@router.get("/{project_id}/lien-releases")
def list_lien_releases(project_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    today = date.today().isoformat()
    rules = PROVINCE_RULES.get(proj.province or "ON", PROVINCE_RULES["ON"])
    releases = db.query(LienRelease).filter(
        LienRelease.project_id == project_id
    ).order_by(LienRelease.lien_expiry_date, LienRelease.created_at.desc()).all()
    result = []
    for r in releases:
        lien_cleared = r.lien_expiry_date and r.lien_expiry_date < today and r.status == "lien_period_running"
        result.append({
            "id": r.id, "draw_id": r.draw_id, "release_type": r.release_type,
            "vendor_name": r.vendor_name, "holdback_amount": r.holdback_amount,
            "lien_expiry_date": r.lien_expiry_date, "release_date": r.release_date,
            "payment_date": r.payment_date,
            "status": "cleared" if lien_cleared else r.status,
            "statutory_declaration_received": r.statutory_declaration_received,
            "days_until_clear": (
                (datetime.strptime(r.lien_expiry_date, "%Y-%m-%d").date() - date.today()).days
                if r.lien_expiry_date and r.lien_expiry_date >= today else None
            ),
            "notes": r.notes,
            "province_lien_days": rules["lien_period_days"],
        })
    return result


@router.post("/{project_id}/lien-releases")
def create_lien_release(project_id: int, body: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    rules = PROVINCE_RULES.get(proj.province or "ON", PROVINCE_RULES["ON"])
    lien_days = rules["lien_period_days"]
    # Auto-calculate lien expiry if last supply date given
    expiry = body.get("lien_expiry_date")
    if not expiry and body.get("last_supply_date"):
        try:
            expiry = (datetime.strptime(body["last_supply_date"], "%Y-%m-%d") + timedelta(days=lien_days)).strftime("%Y-%m-%d")
        except Exception:
            pass
    r = LienRelease(
        org_id=proj.org_id, project_id=project_id,
        draw_id=body.get("draw_id"),
        release_type=body.get("release_type", "progressive"),
        vendor_id=body.get("vendor_id"),
        vendor_name=body.get("vendor_name"),
        holdback_amount=body.get("holdback_amount"),
        lien_expiry_date=expiry,
        release_date=body.get("release_date"),
        payment_date=body.get("payment_date"),
        status=body.get("status", "lien_period_running"),
        statutory_declaration_received=body.get("statutory_declaration_received", False),
        notes=body.get("notes"),
        created_by=user.id,
    )
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "lien_expiry_date": r.lien_expiry_date, "ok": True}


@router.put("/{project_id}/lien-releases/{rel_id}")
def update_lien_release(project_id: int, rel_id: int, body: dict,
                        db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(LienRelease).filter(LienRelease.id == rel_id, LienRelease.project_id == project_id).first()
    if not r: raise HTTPException(404)
    for f in ["release_type","vendor_name","holdback_amount","lien_expiry_date",
              "release_date","payment_date","status","statutory_declaration_received","notes"]:
        if f in body: setattr(r, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/lien-releases/{rel_id}")
def delete_lien_release(project_id: int, rel_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(LienRelease).filter(LienRelease.id == rel_id, LienRelease.project_id == project_id).first()
    if r: db.delete(r); db.commit()
    return {"ok": True}


@router.get("/{project_id}/lien-releases/summary")
def lien_release_summary(project_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    today = date.today().isoformat()
    releases = db.query(LienRelease).filter(LienRelease.project_id == project_id).all()
    total_holdback = sum(r.holdback_amount or 0 for r in releases)
    released = sum(r.holdback_amount or 0 for r in releases if r.status == "released")
    cleared = sum(r.holdback_amount or 0 for r in releases if r.status in ("cleared",) or (r.lien_expiry_date and r.lien_expiry_date < today))
    rules = PROVINCE_RULES.get(proj.province or "ON", PROVINCE_RULES["ON"])
    return {
        "total_holdback_tracked": total_holdback,
        "total_released": released,
        "lien_period_cleared": cleared,
        "pending_release": total_holdback - released,
        "lien_period_days": rules["lien_period_days"],
        "province": proj.province or "ON",
        "act": rules["act"],
        "counts": {
            "total": len(releases),
            "pending": sum(1 for r in releases if r.status == "pending"),
            "lien_running": sum(1 for r in releases if r.status == "lien_period_running"),
            "released": sum(1 for r in releases if r.status == "released"),
        }
    }


# ── Vendor Scorecard ───────────────────────────────────────────────────────────

@router.get("/{project_id}/vendor-scores")
def list_vendor_scores(project_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    scores = db.query(VendorScore).filter(VendorScore.project_id == project_id).order_by(VendorScore.vendor_name).all()
    return [_score_out(s) for s in scores]


def _score_out(s):
    cats = [s.quality, s.timeliness, s.safety_score, s.communication, s.value]
    filled = [c for c in cats if c is not None]
    avg = round(sum(filled) / len(filled), 1) if filled else None
    return {
        "id": s.id, "vendor_id": s.vendor_id, "vendor_name": s.vendor_name,
        "period": s.period, "quality": s.quality, "timeliness": s.timeliness,
        "safety": s.safety_score, "communication": s.communication, "value": s.value,
        "average": avg, "would_rehire": s.would_rehire, "comments": s.comments,
        "created_at": s.created_at.isoformat(),
    }


@router.post("/{project_id}/vendor-scores")
def create_vendor_score(project_id: int, body: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    s = VendorScore(
        org_id=proj.org_id, project_id=project_id,
        vendor_id=body.get("vendor_id"), vendor_name=body["vendor_name"],
        period=body.get("period"),
        quality=body.get("quality"), timeliness=body.get("timeliness"),
        safety_score=body.get("safety"), communication=body.get("communication"),
        value=body.get("value"), would_rehire=body.get("would_rehire"),
        comments=body.get("comments"), rated_by=user.id,
    )
    db.add(s); db.commit(); db.refresh(s)
    return {"id": s.id, "ok": True}


@router.put("/{project_id}/vendor-scores/{score_id}")
def update_vendor_score(project_id: int, score_id: int, body: dict,
                        db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    s = db.query(VendorScore).filter(VendorScore.id == score_id, VendorScore.project_id == project_id).first()
    if not s: raise HTTPException(404)
    for f in ["vendor_name","period","quality","timeliness","safety_score","communication","value","would_rehire","comments"]:
        if f in body: setattr(s, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/vendor-scores/{score_id}")
def delete_vendor_score(project_id: int, score_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    s = db.query(VendorScore).filter(VendorScore.id == score_id, VendorScore.project_id == project_id).first()
    if s: db.delete(s); db.commit()
    return {"ok": True}


