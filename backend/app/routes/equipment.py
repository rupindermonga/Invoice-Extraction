"""Equipment Management — register, daily usage logs, utilization report."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from ..database import SessionLocal
from ..dependencies import get_current_user, get_current_org, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import Equipment, EquipmentLog, Project

router = APIRouter(prefix="/api", tags=["equipment"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Equipment Register (org-level) ─────────────────────────────────────────────

@router.get("/equipment")
def list_equipment(org_ctx=Depends(get_current_org), db: Session = Depends(get_db)):
    org, _ = org_ctx
    equip = db.query(Equipment).filter(Equipment.org_id == org.id).order_by(Equipment.name).all()
    from datetime import date
    today = date.today().isoformat()
    return [{
        "id": e.id, "equipment_code": e.equipment_code, "name": e.name,
        "equipment_type": e.equipment_type, "make": e.make, "model": e.model,
        "year": e.year, "ownership": e.ownership, "daily_rate": e.daily_rate,
        "hourly_rate": e.hourly_rate, "status": e.status,
        "current_project_id": e.current_project_id,
        "operator_name": e.operator_name,
        "next_service_date": e.next_service_date,
        "insurance_expiry": e.insurance_expiry,
        "service_overdue": e.next_service_date and e.next_service_date < today,
        "insurance_expired": e.insurance_expiry and e.insurance_expiry < today,
        "notes": e.notes,
        "total_hours": sum(l.hours_used or 0 for l in e.usage_logs),
    } for e in equip]


@router.post("/equipment")
def create_equipment(body: dict, org_ctx=Depends(get_current_org), db: Session = Depends(get_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES:
        raise HTTPException(403)
    last = db.query(Equipment).filter(Equipment.org_id == org.id).order_by(Equipment.id.desc()).first()
    code = f"EQ-{((int(last.equipment_code.split('-')[1]) if last and last.equipment_code else 0) + 1):03d}"
    e = Equipment(
        org_id=org.id,
        equipment_code=body.get("equipment_code", code),
        name=body["name"],
        equipment_type=body.get("equipment_type"),
        make=body.get("make"), model=body.get("model"), year=body.get("year"),
        serial_number=body.get("serial_number"),
        ownership=body.get("ownership", "owned"),
        daily_rate=body.get("daily_rate"), hourly_rate=body.get("hourly_rate"),
        status=body.get("status", "available"),
        current_project_id=body.get("current_project_id"),
        operator_name=body.get("operator_name"),
        next_service_date=body.get("next_service_date"),
        insurance_expiry=body.get("insurance_expiry"),
        notes=body.get("notes"),
    )
    db.add(e); db.commit(); db.refresh(e)
    return {"id": e.id, "equipment_code": e.equipment_code, "ok": True}


@router.put("/equipment/{eq_id}")
def update_equipment(eq_id: int, body: dict, org_ctx=Depends(get_current_org), db: Session = Depends(get_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    e = db.query(Equipment).filter(Equipment.id == eq_id, Equipment.org_id == org.id).first()
    if not e: raise HTTPException(404)
    for f in ["name","equipment_type","make","model","year","serial_number","ownership",
              "daily_rate","hourly_rate","status","current_project_id","operator_name",
              "next_service_date","insurance_expiry","notes"]:
        if f in body: setattr(e, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/equipment/{eq_id}")
def delete_equipment(eq_id: int, org_ctx=Depends(get_current_org), db: Session = Depends(get_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    e = db.query(Equipment).filter(Equipment.id == eq_id, Equipment.org_id == org.id).first()
    if e: db.delete(e); db.commit()
    return {"ok": True}


# ── Equipment Logs ─────────────────────────────────────────────────────────────

@router.get("/equipment/{eq_id}/logs")
def list_logs(eq_id: int, org_ctx=Depends(get_current_org), db: Session = Depends(get_db)):
    org, _ = org_ctx
    e = db.query(Equipment).filter(Equipment.id == eq_id, Equipment.org_id == org.id).first()
    if not e: raise HTTPException(404)
    logs = db.query(EquipmentLog).filter(EquipmentLog.equipment_id == eq_id).order_by(EquipmentLog.log_date.desc()).all()
    return [{"id": l.id, "log_date": l.log_date, "log_type": l.log_type,
             "hours_used": l.hours_used, "fuel_litres": l.fuel_litres,
             "operator_name": l.operator_name, "work_description": l.work_description,
             "cost": l.cost, "notes": l.notes, "project_id": l.project_id} for l in logs]


@router.post("/equipment/{eq_id}/logs")
def add_log(eq_id: int, body: dict, org_ctx=Depends(get_current_org),
            db: Session = Depends(get_db), user=Depends(get_current_user)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    e = db.query(Equipment).filter(Equipment.id == eq_id, Equipment.org_id == org.id).first()
    if not e: raise HTTPException(404)
    l = EquipmentLog(
        equipment_id=eq_id, org_id=org.id,
        project_id=body.get("project_id"),
        log_date=body["log_date"],
        log_type=body.get("log_type", "usage"),
        hours_used=body.get("hours_used", 0),
        fuel_litres=body.get("fuel_litres"),
        operator_name=body.get("operator_name"),
        work_description=body.get("work_description"),
        cost=body.get("cost"),
        notes=body.get("notes"),
        created_by=user.id,
    )
    db.add(l); db.commit(); db.refresh(l)
    return {"id": l.id, "ok": True}


@router.delete("/equipment/{eq_id}/logs/{log_id}")
def delete_log(eq_id: int, log_id: int, org_ctx=Depends(get_current_org), db: Session = Depends(get_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    l = db.query(EquipmentLog).filter(EquipmentLog.id == log_id, EquipmentLog.equipment_id == eq_id).first()
    if l: db.delete(l); db.commit()
    return {"ok": True}


# ── Utilization Report ─────────────────────────────────────────────────────────

@router.get("/equipment/utilization-report")
def utilization_report(org_ctx=Depends(get_current_org), db: Session = Depends(get_db)):
    org, _ = org_ctx
    equip = db.query(Equipment).filter(Equipment.org_id == org.id, Equipment.status != "retired").all()
    report = []
    for e in equip:
        logs = e.usage_logs
        total_hours = sum(l.hours_used or 0 for l in logs)
        total_cost = sum(l.cost or 0 for l in logs)
        total_fuel = sum(l.fuel_litres or 0 for l in logs)
        maint_count = sum(1 for l in logs if l.log_type in ("maintenance","repair"))
        report.append({
            "id": e.id, "name": e.name, "equipment_type": e.equipment_type,
            "ownership": e.ownership, "status": e.status,
            "total_hours": round(total_hours, 1),
            "total_maintenance_cost": round(total_cost, 2),
            "total_fuel_litres": round(total_fuel, 1),
            "maintenance_events": maint_count,
            "hourly_rate": e.hourly_rate,
            "estimated_revenue": round(total_hours * (e.hourly_rate or 0), 2),
        })
    report.sort(key=lambda x: -x["total_hours"])
    return {
        "equipment": report,
        "summary": {
            "total_units": len(report),
            "total_hours": sum(r["total_hours"] for r in report),
            "total_maintenance_cost": sum(r["total_maintenance_cost"] for r in report),
            "in_use": sum(1 for e in equip if e.status == "in_use"),
            "in_maintenance": sum(1 for e in equip if e.status == "maintenance"),
        }
    }
