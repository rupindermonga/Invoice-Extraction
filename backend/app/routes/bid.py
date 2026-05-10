"""Bid Management / Preconstruction — bid packages, responses, leveling, award."""
import secrets
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..dependencies import get_current_user, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import BidPackage, BidResponse, Project, OrgVendor

router = APIRouter(prefix="/api/project", tags=["bid"])


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


# ── Bid Packages ───────────────────────────────────────────────────────────────

@router.get("/{project_id}/bid-packages")
def list_bid_packages(project_id: int, db: Session = Depends(get_db),
                      user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    packages = db.query(BidPackage).filter(
        BidPackage.project_id == project_id
    ).order_by(BidPackage.issue_date.desc(), BidPackage.package_number).all()
    return [{
        "id": p.id, "package_number": p.package_number, "title": p.title,
        "trade_category": p.trade_category, "issue_date": p.issue_date,
        "due_date": p.due_date, "estimated_value": p.estimated_value,
        "status": p.status, "notes": p.notes,
        "response_count": len(p.responses),
        "submitted_count": sum(1 for r in p.responses if r.status in ("submitted","shortlisted","awarded")),
        "lowest_bid": min((r.total_amount for r in p.responses if r.total_amount), default=None),
        "created_at": p.created_at.isoformat(),
    } for p in packages]


@router.post("/{project_id}/bid-packages")
def create_bid_package(project_id: int, body: dict, db: Session = Depends(get_db),
                       user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    last = db.query(BidPackage).filter(BidPackage.project_id == project_id).order_by(BidPackage.id.desc()).first()
    num = f"BP-{((int(last.package_number.split('-')[1]) if last and last.package_number else 0) + 1):03d}"
    p = BidPackage(
        org_id=proj.org_id, project_id=project_id,
        package_number=body.get("package_number", num),
        title=body["title"],
        description=body.get("description"),
        trade_category=body.get("trade_category"),
        issue_date=body.get("issue_date"),
        due_date=body.get("due_date"),
        estimated_value=body.get("estimated_value"),
        status=body.get("status", "draft"),
        notes=body.get("notes"),
        created_by=user.id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "package_number": p.package_number, "ok": True}


@router.put("/{project_id}/bid-packages/{pkg_id}")
def update_bid_package(project_id: int, pkg_id: int, body: dict,
                       db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    p = db.query(BidPackage).filter(BidPackage.id == pkg_id, BidPackage.project_id == project_id).first()
    if not p:
        raise HTTPException(404)
    for field in ["title", "description", "trade_category", "issue_date", "due_date",
                  "estimated_value", "status", "notes"]:
        if field in body:
            setattr(p, field, body[field])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/bid-packages/{pkg_id}")
def delete_bid_package(project_id: int, pkg_id: int, db: Session = Depends(get_db),
                       user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    p = db.query(BidPackage).filter(BidPackage.id == pkg_id, BidPackage.project_id == project_id).first()
    if p:
        db.delete(p)
        db.commit()
    return {"ok": True}


# ── Bid Responses ──────────────────────────────────────────────────────────────

@router.get("/{project_id}/bid-packages/{pkg_id}/responses")
def list_responses(project_id: int, pkg_id: int, db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    responses = db.query(BidResponse).filter(
        BidResponse.package_id == pkg_id
    ).order_by(BidResponse.total_amount.asc().nullslast()).all()
    return [{"id": r.id, "vendor_name": r.vendor_name, "contact_email": r.contact_email,
             "submitted_date": r.submitted_date, "total_amount": r.total_amount,
             "inclusions": r.inclusions, "exclusions": r.exclusions,
             "qualifications": r.qualifications, "status": r.status,
             "invite_token": r.invite_token, "notes": r.notes} for r in responses]


@router.post("/{project_id}/bid-packages/{pkg_id}/responses")
def add_response(project_id: int, pkg_id: int, body: dict,
                 db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    pkg = db.query(BidPackage).filter(BidPackage.id == pkg_id, BidPackage.project_id == project_id).first()
    if not pkg:
        raise HTTPException(404, "Bid package not found")
    r = BidResponse(
        package_id=pkg_id, org_id=proj.org_id, project_id=project_id,
        vendor_id=body.get("vendor_id"),
        vendor_name=body["vendor_name"],
        contact_email=body.get("contact_email"),
        submitted_date=body.get("submitted_date"),
        total_amount=body.get("total_amount"),
        inclusions=body.get("inclusions"),
        exclusions=body.get("exclusions"),
        qualifications=body.get("qualifications"),
        status=body.get("status", "invited"),
        notes=body.get("notes"),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"id": r.id, "ok": True}


@router.put("/{project_id}/bid-packages/{pkg_id}/responses/{resp_id}")
def update_response(project_id: int, pkg_id: int, resp_id: int, body: dict,
                    db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(BidResponse).filter(
        BidResponse.id == resp_id, BidResponse.package_id == pkg_id
    ).first()
    if not r:
        raise HTTPException(404)
    for field in ["vendor_name", "contact_email", "submitted_date", "total_amount",
                  "inclusions", "exclusions", "qualifications", "status", "notes"]:
        if field in body:
            setattr(r, field, body[field])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/bid-packages/{pkg_id}/responses/{resp_id}")
def delete_response(project_id: int, pkg_id: int, resp_id: int,
                    db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(BidResponse).filter(
        BidResponse.id == resp_id, BidResponse.package_id == pkg_id
    ).first()
    if r:
        db.delete(r)
        db.commit()
    return {"ok": True}


# ── Bid Leveling View ──────────────────────────────────────────────────────────

@router.get("/{project_id}/bid-packages/{pkg_id}/leveling")
def bid_leveling(project_id: int, pkg_id: int, db: Session = Depends(get_db),
                 user=Depends(get_current_user)):
    """Bid leveling comparison — ranks bids, flags spread vs estimate."""
    _get_project(project_id, user, db)
    pkg = db.query(BidPackage).filter(BidPackage.id == pkg_id, BidPackage.project_id == project_id).first()
    if not pkg:
        raise HTTPException(404)
    responses = db.query(BidResponse).filter(
        BidResponse.package_id == pkg_id,
        BidResponse.status.in_(["submitted", "shortlisted", "awarded"])
    ).order_by(BidResponse.total_amount.asc().nullslast()).all()

    submitted = [r for r in responses if r.total_amount is not None]
    low = submitted[0].total_amount if submitted else None
    high = submitted[-1].total_amount if submitted else None
    spread = round((high - low) / low * 100, 1) if low and high and low > 0 else None

    variance_from_estimate = None
    if low and pkg.estimated_value and pkg.estimated_value > 0:
        variance_from_estimate = round((low - pkg.estimated_value) / pkg.estimated_value * 100, 1)

    return {
        "package": {"id": pkg.id, "title": pkg.title, "estimated_value": pkg.estimated_value, "status": pkg.status},
        "bid_count": len(submitted),
        "low_bid": low, "high_bid": high,
        "spread_pct": spread,
        "variance_from_estimate_pct": variance_from_estimate,
        "bids": [{"rank": i+1, "id": r.id, "vendor_name": r.vendor_name,
                  "total_amount": r.total_amount, "status": r.status,
                  "premium_pct": round((r.total_amount - low) / low * 100, 1) if low and r.total_amount else None,
                  "vs_estimate_pct": round((r.total_amount - pkg.estimated_value) / pkg.estimated_value * 100, 1) if pkg.estimated_value and r.total_amount else None}
                 for i, r in enumerate(submitted)],
    }


# ── Invite Token ───────────────────────────────────────────────────────────────

@router.post("/{project_id}/bid-packages/{pkg_id}/invite")
def generate_invite(project_id: int, pkg_id: int, body: dict,
                    db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Generate or get existing invite token for a bid response."""
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(BidResponse).filter(
        BidResponse.id == body["response_id"], BidResponse.package_id == pkg_id
    ).first()
    if not r:
        raise HTTPException(404)
    if not r.invite_token:
        r.invite_token = secrets.token_urlsafe(24)
        db.commit()
    return {"invite_url": f"/bid/{r.invite_token}", "token": r.invite_token}


# ── Public Bid Portal ──────────────────────────────────────────────────────────

_bid_portal_router = APIRouter(tags=["bid-portal"])

@_bid_portal_router.get("/bid/{token}")
def bid_portal_api(token: str, db: Session = Depends(get_db)):
    """Public API for subs to view package and submit their bid."""
    r = db.query(BidResponse).filter(BidResponse.invite_token == token).first()
    if not r:
        raise HTTPException(404, "Bid invitation not found or expired.")
    pkg = r.package
    proj = db.query(Project).filter(Project.id == r.project_id).first()
    return {
        "project_name": proj.name if proj else "",
        "package_number": pkg.package_number,
        "title": pkg.title,
        "description": pkg.description,
        "trade_category": pkg.trade_category,
        "due_date": pkg.due_date,
        "vendor_name": r.vendor_name,
        "status": r.status,
        "existing_amount": r.total_amount,
        "existing_inclusions": r.inclusions,
        "existing_exclusions": r.exclusions,
        "existing_qualifications": r.qualifications,
    }

@_bid_portal_router.put("/bid/{token}/submit")
def submit_bid(token: str, body: dict, db: Session = Depends(get_db)):
    """Sub submits their bid through the public portal."""
    r = db.query(BidResponse).filter(BidResponse.invite_token == token).first()
    if not r:
        raise HTTPException(404)
    if r.status == "awarded":
        raise HTTPException(400, "This bid has already been awarded.")
    r.total_amount = body.get("total_amount")
    r.inclusions = body.get("inclusions")
    r.exclusions = body.get("exclusions")
    r.qualifications = body.get("qualifications")
    r.submitted_date = datetime.utcnow().strftime("%Y-%m-%d")
    r.status = "submitted"
    db.commit()
    return {"ok": True, "message": "Your bid has been submitted successfully."}
