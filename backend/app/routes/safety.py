"""Safety Management: Incidents, Toolbox Talks, Warranty Items, Bond Registry."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from ..database import SessionLocal
from ..dependencies import get_current_user, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import SafetyIncident, ToolboxTalk, WarrantyItem, Bond, Project

router = APIRouter(prefix="/api/project", tags=["safety"])


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


# ── Safety Incidents ────────────────────────────────────────────────────────────

@router.get("/{project_id}/safety/incidents")
def list_incidents(project_id: int, db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    rows = db.query(SafetyIncident).filter(
        SafetyIncident.project_id == project_id
    ).order_by(SafetyIncident.incident_date.desc()).all()
    return [{"id": r.id, "incident_date": r.incident_date, "incident_type": r.incident_type,
             "severity": r.severity, "description": r.description, "location": r.location,
             "persons_involved": r.persons_involved, "immediate_actions": r.immediate_actions,
             "root_cause": r.root_cause, "corrective_actions": r.corrective_actions,
             "wsib_reportable": r.wsib_reportable, "wsib_reported_date": r.wsib_reported_date,
             "mol_reportable": r.mol_reportable, "mol_reported_date": r.mol_reported_date,
             "status": r.status, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/{project_id}/safety/incidents")
def create_incident(project_id: int, body: dict, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = SafetyIncident(
        org_id=proj.org_id, project_id=project_id,
        incident_date=body["incident_date"],
        incident_type=body.get("incident_type", "near_miss"),
        severity=body.get("severity", "low"),
        description=body["description"],
        location=body.get("location"),
        persons_involved=body.get("persons_involved"),
        immediate_actions=body.get("immediate_actions"),
        root_cause=body.get("root_cause"),
        corrective_actions=body.get("corrective_actions"),
        wsib_reportable=body.get("wsib_reportable", False),
        wsib_reported_date=body.get("wsib_reported_date"),
        mol_reportable=body.get("mol_reportable", False),
        mol_reported_date=body.get("mol_reported_date"),
        status=body.get("status", "open"),
        created_by=user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"id": r.id, "ok": True}


@router.put("/{project_id}/safety/incidents/{incident_id}")
def update_incident(project_id: int, incident_id: int, body: dict,
                    db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(SafetyIncident).filter(
        SafetyIncident.id == incident_id, SafetyIncident.project_id == project_id
    ).first()
    if not r:
        raise HTTPException(404)
    for field in ["incident_date", "incident_type", "severity", "description", "location",
                  "persons_involved", "immediate_actions", "root_cause", "corrective_actions",
                  "wsib_reportable", "wsib_reported_date", "mol_reportable",
                  "mol_reported_date", "status"]:
        if field in body:
            setattr(r, field, body[field])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/safety/incidents/{incident_id}")
def delete_incident(project_id: int, incident_id: int, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(SafetyIncident).filter(
        SafetyIncident.id == incident_id, SafetyIncident.project_id == project_id
    ).first()
    if r:
        db.delete(r)
        db.commit()
    return {"ok": True}


# ── Toolbox Talks ───────────────────────────────────────────────────────────────

@router.get("/{project_id}/safety/toolbox-talks")
def list_toolbox_talks(project_id: int, db: Session = Depends(get_db),
                       user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    rows = db.query(ToolboxTalk).filter(
        ToolboxTalk.project_id == project_id
    ).order_by(ToolboxTalk.talk_date.desc()).all()
    return [{"id": r.id, "talk_date": r.talk_date, "topic": r.topic,
             "facilitator": r.facilitator, "attendee_count": r.attendee_count,
             "attendees": r.attendees, "duration_minutes": r.duration_minutes,
             "notes": r.notes, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/{project_id}/safety/toolbox-talks")
def create_toolbox_talk(project_id: int, body: dict, db: Session = Depends(get_db),
                        user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = ToolboxTalk(
        org_id=proj.org_id, project_id=project_id,
        talk_date=body["talk_date"],
        topic=body["topic"],
        facilitator=body.get("facilitator"),
        attendee_count=body.get("attendee_count", 0),
        attendees=body.get("attendees"),
        duration_minutes=body.get("duration_minutes"),
        notes=body.get("notes"),
        created_by=user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"id": r.id, "ok": True}


@router.put("/{project_id}/safety/toolbox-talks/{talk_id}")
def update_toolbox_talk(project_id: int, talk_id: int, body: dict,
                        db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(ToolboxTalk).filter(
        ToolboxTalk.id == talk_id, ToolboxTalk.project_id == project_id
    ).first()
    if not r:
        raise HTTPException(404)
    for field in ["talk_date", "topic", "facilitator", "attendee_count",
                  "attendees", "duration_minutes", "notes"]:
        if field in body:
            setattr(r, field, body[field])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/safety/toolbox-talks/{talk_id}")
def delete_toolbox_talk(project_id: int, talk_id: int, db: Session = Depends(get_db),
                        user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(ToolboxTalk).filter(
        ToolboxTalk.id == talk_id, ToolboxTalk.project_id == project_id
    ).first()
    if r:
        db.delete(r)
        db.commit()
    return {"ok": True}


# ── Safety Summary ──────────────────────────────────────────────────────────────

@router.get("/{project_id}/safety/summary")
def safety_summary(project_id: int, db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    incidents = db.query(SafetyIncident).filter(SafetyIncident.project_id == project_id).all()
    talks = db.query(ToolboxTalk).filter(ToolboxTalk.project_id == project_id).all()
    return {
        "total_incidents": len(incidents),
        "open_incidents": sum(1 for i in incidents if i.status == "open"),
        "critical_incidents": sum(1 for i in incidents if i.severity == "critical"),
        "wsib_reportable": sum(1 for i in incidents if i.wsib_reportable),
        "mol_reportable": sum(1 for i in incidents if i.mol_reportable),
        "total_toolbox_talks": len(talks),
        "total_attendees": sum(t.attendee_count or 0 for t in talks),
        "incidents_by_type": {t: sum(1 for i in incidents if i.incident_type == t)
                               for t in ["injury", "near_miss", "property_damage", "first_aid", "environmental"]},
    }


# ── Warranty Items ──────────────────────────────────────────────────────────────

@router.get("/{project_id}/warranty")
def list_warranty(project_id: int, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    rows = db.query(WarrantyItem).filter(
        WarrantyItem.project_id == project_id
    ).order_by(WarrantyItem.reported_date.desc()).all()
    return [{"id": r.id, "item_number": r.item_number, "category": r.category,
             "description": r.description, "location": r.location,
             "reported_date": r.reported_date, "warranty_type": r.warranty_type,
             "homeowner_name": r.homeowner_name, "status": r.status,
             "assigned_to": r.assigned_to, "scheduled_date": r.scheduled_date,
             "resolved_date": r.resolved_date, "notes": r.notes,
             "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/{project_id}/warranty")
def create_warranty(project_id: int, body: dict, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    last = db.query(WarrantyItem).filter(
        WarrantyItem.project_id == project_id
    ).order_by(WarrantyItem.id.desc()).first()
    num = f"W-{((int(last.item_number.split('-')[1]) if last and last.item_number else 0) + 1):03d}"
    r = WarrantyItem(
        org_id=proj.org_id, project_id=project_id,
        item_number=body.get("item_number", num),
        category=body.get("category", "other"),
        description=body["description"],
        location=body.get("location"),
        reported_date=body.get("reported_date"),
        warranty_type=body.get("warranty_type", "1year"),
        homeowner_name=body.get("homeowner_name"),
        status=body.get("status", "open"),
        assigned_to=body.get("assigned_to"),
        scheduled_date=body.get("scheduled_date"),
        resolved_date=body.get("resolved_date"),
        notes=body.get("notes"),
        created_by=user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"id": r.id, "item_number": r.item_number, "ok": True}


@router.put("/{project_id}/warranty/{item_id}")
def update_warranty(project_id: int, item_id: int, body: dict,
                    db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(WarrantyItem).filter(
        WarrantyItem.id == item_id, WarrantyItem.project_id == project_id
    ).first()
    if not r:
        raise HTTPException(404)
    for field in ["category", "description", "location", "reported_date", "warranty_type",
                  "homeowner_name", "status", "assigned_to", "scheduled_date",
                  "resolved_date", "notes"]:
        if field in body:
            setattr(r, field, body[field])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/warranty/{item_id}")
def delete_warranty(project_id: int, item_id: int, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(WarrantyItem).filter(
        WarrantyItem.id == item_id, WarrantyItem.project_id == project_id
    ).first()
    if r:
        db.delete(r)
        db.commit()
    return {"ok": True}


# ── Bond Registry ───────────────────────────────────────────────────────────────

@router.get("/{project_id}/bonds")
def list_bonds(project_id: int, db: Session = Depends(get_db),
               user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    from datetime import date
    today = date.today().isoformat()
    rows = db.query(Bond).filter(Bond.project_id == project_id).order_by(Bond.bond_type).all()
    return [{"id": r.id, "vendor_id": r.vendor_id, "vendor_name": r.vendor_name,
             "bond_type": r.bond_type, "bond_number": r.bond_number,
             "surety_company": r.surety_company, "bond_amount": r.bond_amount,
             "effective_date": r.effective_date, "expiry_date": r.expiry_date,
             "status": "expired" if (r.expiry_date and r.expiry_date < today and r.status == "active") else r.status,
             "notes": r.notes, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/{project_id}/bonds")
def create_bond(project_id: int, body: dict, db: Session = Depends(get_db),
                user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = Bond(
        org_id=proj.org_id, project_id=project_id,
        vendor_id=body.get("vendor_id"),
        vendor_name=body.get("vendor_name"),
        bond_type=body.get("bond_type", "performance"),
        bond_number=body.get("bond_number"),
        surety_company=body.get("surety_company"),
        bond_amount=body.get("bond_amount"),
        effective_date=body.get("effective_date"),
        expiry_date=body.get("expiry_date"),
        status=body.get("status", "active"),
        notes=body.get("notes"),
        created_by=user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"id": r.id, "ok": True}


@router.put("/{project_id}/bonds/{bond_id}")
def update_bond(project_id: int, bond_id: int, body: dict,
                db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(Bond).filter(Bond.id == bond_id, Bond.project_id == project_id).first()
    if not r:
        raise HTTPException(404)
    for field in ["vendor_name", "bond_type", "bond_number", "surety_company",
                  "bond_amount", "effective_date", "expiry_date", "status", "notes"]:
        if field in body:
            setattr(r, field, body[field])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/bonds/{bond_id}")
def delete_bond(project_id: int, bond_id: int, db: Session = Depends(get_db),
                user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(Bond).filter(Bond.id == bond_id, Bond.project_id == project_id).first()
    if r:
        db.delete(r)
        db.commit()
    return {"ok": True}
