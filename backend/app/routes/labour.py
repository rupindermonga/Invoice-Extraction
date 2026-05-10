"""Labour Time Tracking — crew timecards, labour cost report."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from ..database import SessionLocal
from ..dependencies import get_current_user, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import Timecard, Project, CostCategory

router = APIRouter(prefix="/api/project", tags=["labour"])


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


@router.get("/{project_id}/timecards")
def list_timecards(project_id: int, date_from: str = None, date_to: str = None,
                   worker: str = None, db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    q = db.query(Timecard).filter(Timecard.project_id == project_id)
    if date_from:
        q = q.filter(Timecard.work_date >= date_from)
    if date_to:
        q = q.filter(Timecard.work_date <= date_to)
    if worker:
        q = q.filter(Timecard.worker_name.ilike(f"%{worker}%"))
    rows = q.order_by(Timecard.work_date.desc(), Timecard.worker_name).all()

    result = []
    for r in rows:
        total_hours = (r.regular_hours or 0) + (r.overtime_hours or 0) + (r.double_time_hours or 0)
        base_cost = (
            (r.regular_hours or 0) * (r.hourly_rate or 0) +
            (r.overtime_hours or 0) * (r.hourly_rate or 0) * 1.5 +
            (r.double_time_hours or 0) * (r.hourly_rate or 0) * 2.0
        )
        total_cost = base_cost * (1 + (r.burden_pct or 0) / 100)
        result.append({
            "id": r.id, "worker_name": r.worker_name, "trade": r.trade,
            "classification": r.classification, "work_date": r.work_date,
            "regular_hours": r.regular_hours, "overtime_hours": r.overtime_hours,
            "double_time_hours": r.double_time_hours, "total_hours": round(total_hours, 2),
            "hourly_rate": r.hourly_rate, "burden_pct": r.burden_pct,
            "total_cost": round(total_cost, 2),
            "cost_category_id": r.cost_category_id, "work_description": r.work_description,
            "created_at": r.created_at.isoformat(),
        })
    return result


@router.post("/{project_id}/timecards")
def create_timecard(project_id: int, body: dict, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = Timecard(
        org_id=proj.org_id, project_id=project_id,
        worker_name=body["worker_name"],
        trade=body.get("trade"),
        classification=body.get("classification"),
        work_date=body["work_date"],
        regular_hours=body.get("regular_hours", 0),
        overtime_hours=body.get("overtime_hours", 0),
        double_time_hours=body.get("double_time_hours", 0),
        hourly_rate=body.get("hourly_rate"),
        burden_pct=body.get("burden_pct", 0),
        cost_category_id=body.get("cost_category_id"),
        work_description=body.get("work_description"),
        created_by=user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"id": r.id, "ok": True}


@router.put("/{project_id}/timecards/{tc_id}")
def update_timecard(project_id: int, tc_id: int, body: dict,
                    db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(Timecard).filter(Timecard.id == tc_id, Timecard.project_id == project_id).first()
    if not r:
        raise HTTPException(404)
    for field in ["worker_name", "trade", "classification", "work_date",
                  "regular_hours", "overtime_hours", "double_time_hours",
                  "hourly_rate", "burden_pct", "cost_category_id", "work_description"]:
        if field in body:
            setattr(r, field, body[field])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/timecards/{tc_id}")
def delete_timecard(project_id: int, tc_id: int, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(Timecard).filter(Timecard.id == tc_id, Timecard.project_id == project_id).first()
    if r:
        db.delete(r)
        db.commit()
    return {"ok": True}


@router.get("/{project_id}/labour-report")
def labour_report(project_id: int, date_from: str = None, date_to: str = None,
                  db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Labour cost report — by worker, by trade, by week."""
    _get_project(project_id, user, db)
    q = db.query(Timecard).filter(Timecard.project_id == project_id)
    if date_from:
        q = q.filter(Timecard.work_date >= date_from)
    if date_to:
        q = q.filter(Timecard.work_date <= date_to)
    rows = q.all()

    total_regular = sum(r.regular_hours or 0 for r in rows)
    total_ot = sum(r.overtime_hours or 0 for r in rows)
    total_dt = sum(r.double_time_hours or 0 for r in rows)
    total_hours = total_regular + total_ot + total_dt

    def row_cost(r):
        base = ((r.regular_hours or 0) * (r.hourly_rate or 0) +
                (r.overtime_hours or 0) * (r.hourly_rate or 0) * 1.5 +
                (r.double_time_hours or 0) * (r.hourly_rate or 0) * 2.0)
        return base * (1 + (r.burden_pct or 0) / 100)

    total_cost = sum(row_cost(r) for r in rows)

    # By trade
    trades = {}
    for r in rows:
        t = r.trade or "Unclassified"
        if t not in trades:
            trades[t] = {"hours": 0, "cost": 0, "workers": set()}
        trades[t]["hours"] += (r.regular_hours or 0) + (r.overtime_hours or 0) + (r.double_time_hours or 0)
        trades[t]["cost"] += row_cost(r)
        trades[t]["workers"].add(r.worker_name)
    by_trade = [{"trade": t, "hours": round(v["hours"], 2),
                 "cost": round(v["cost"], 2), "worker_count": len(v["workers"])}
                for t, v in sorted(trades.items(), key=lambda x: -x[1]["cost"])]

    # By worker (top 10)
    workers = {}
    for r in rows:
        w = r.worker_name
        if w not in workers:
            workers[w] = {"hours": 0, "cost": 0, "trade": r.trade}
        workers[w]["hours"] += (r.regular_hours or 0) + (r.overtime_hours or 0) + (r.double_time_hours or 0)
        workers[w]["cost"] += row_cost(r)
    by_worker = sorted([{"worker": w, "trade": v["trade"], "hours": round(v["hours"], 2),
                          "cost": round(v["cost"], 2)} for w, v in workers.items()],
                       key=lambda x: -x["cost"])[:20]

    return {
        "total_hours": round(total_hours, 2),
        "total_regular_hours": round(total_regular, 2),
        "total_overtime_hours": round(total_ot, 2),
        "total_double_time_hours": round(total_dt, 2),
        "total_cost": round(total_cost, 2),
        "timecard_count": len(rows),
        "worker_count": len(workers),
        "by_trade": by_trade,
        "by_worker": by_worker,
    }
