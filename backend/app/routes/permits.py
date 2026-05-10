"""Permit Register and Municipal Inspection Workflow."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, date

from ..database import SessionLocal
from ..dependencies import get_current_user, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import Permit, PermitInspection, Project

router = APIRouter(prefix="/api/project", tags=["permits"])


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


# ── Permits ────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/permits")
def list_permits(project_id: int, db: Session = Depends(get_db),
                 user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    permits = db.query(Permit).filter(
        Permit.project_id == project_id
    ).order_by(Permit.permit_type, Permit.created_at.desc()).all()

    today = date.today().isoformat()
    result = []
    for p in permits:
        is_expired = p.expiry_date and p.expiry_date < today and p.status not in ("closed", "revoked")
        expiry_warning = (p.expiry_date and p.expiry_date >= today and
                          p.expiry_date <= (date.today().isoformat()[:8] + "31"))  # rough 30-day check
        insp = db.query(PermitInspection).filter(PermitInspection.permit_id == p.id).all()
        result.append({
            "id": p.id, "permit_type": p.permit_type, "permit_number": p.permit_number,
            "description": p.description, "authority": p.authority,
            "application_date": p.application_date, "issued_date": p.issued_date,
            "expiry_date": p.expiry_date, "status": "expired" if is_expired else p.status,
            "fee_paid": p.fee_paid, "notes": p.notes,
            "inspection_count": len(insp),
            "open_inspections": sum(1 for i in insp if i.result == "pending"),
            "failed_inspections": sum(1 for i in insp if i.result == "failed"),
            "created_at": p.created_at.isoformat(),
        })
    return result


@router.post("/{project_id}/permits")
def create_permit(project_id: int, body: dict, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    p = Permit(
        org_id=proj.org_id, project_id=project_id,
        permit_type=body.get("permit_type", "building"),
        permit_number=body.get("permit_number"),
        description=body["description"],
        authority=body.get("authority"),
        application_date=body.get("application_date"),
        issued_date=body.get("issued_date"),
        expiry_date=body.get("expiry_date"),
        status=body.get("status", "pending"),
        fee_paid=body.get("fee_paid"),
        file_path=body.get("file_path"),
        notes=body.get("notes"),
        created_by=user.id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "ok": True}


@router.put("/{project_id}/permits/{permit_id}")
def update_permit(project_id: int, permit_id: int, body: dict,
                  db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    p = db.query(Permit).filter(Permit.id == permit_id, Permit.project_id == project_id).first()
    if not p:
        raise HTTPException(404)
    for field in ["permit_type", "permit_number", "description", "authority",
                  "application_date", "issued_date", "expiry_date", "status",
                  "fee_paid", "notes"]:
        if field in body:
            setattr(p, field, body[field])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/permits/{permit_id}")
def delete_permit(project_id: int, permit_id: int, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    p = db.query(Permit).filter(Permit.id == permit_id, Permit.project_id == project_id).first()
    if p:
        db.delete(p)
        db.commit()
    return {"ok": True}


# ── Permit Inspections ─────────────────────────────────────────────────────────

@router.get("/{project_id}/permits/{permit_id}/inspections")
def list_inspections(project_id: int, permit_id: int, db: Session = Depends(get_db),
                     user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    insp = db.query(PermitInspection).filter(
        PermitInspection.permit_id == permit_id
    ).order_by(PermitInspection.scheduled_date.desc()).all()
    return [{"id": i.id, "inspection_type": i.inspection_type,
             "scheduled_date": i.scheduled_date, "completed_date": i.completed_date,
             "inspector_name": i.inspector_name, "result": i.result,
             "deficiencies": i.deficiencies, "notes": i.notes} for i in insp]


@router.post("/{project_id}/permits/{permit_id}/inspections")
def create_inspection(project_id: int, permit_id: int, body: dict,
                      db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    permit = db.query(Permit).filter(Permit.id == permit_id, Permit.project_id == project_id).first()
    if not permit:
        raise HTTPException(404, "Permit not found")
    i = PermitInspection(
        permit_id=permit_id, org_id=proj.org_id, project_id=project_id,
        inspection_type=body["inspection_type"],
        scheduled_date=body.get("scheduled_date"),
        completed_date=body.get("completed_date"),
        inspector_name=body.get("inspector_name"),
        result=body.get("result", "pending"),
        deficiencies=body.get("deficiencies"),
        notes=body.get("notes"),
    )
    db.add(i)
    db.commit()
    db.refresh(i)
    return {"id": i.id, "ok": True}


@router.put("/{project_id}/permits/{permit_id}/inspections/{insp_id}")
def update_inspection(project_id: int, permit_id: int, insp_id: int, body: dict,
                      db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    i = db.query(PermitInspection).filter(
        PermitInspection.id == insp_id, PermitInspection.permit_id == permit_id
    ).first()
    if not i:
        raise HTTPException(404)
    for field in ["inspection_type", "scheduled_date", "completed_date",
                  "inspector_name", "result", "deficiencies", "notes"]:
        if field in body:
            setattr(i, field, body[field])
    db.commit()
    return {"ok": True}
