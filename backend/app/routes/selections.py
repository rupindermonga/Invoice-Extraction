"""Client Selections Portal — finish selections, allowances, client approvals."""
import secrets
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..dependencies import get_current_user, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import (
    ClientSelectionCategory, ClientSelection, ClientSelectionToken, Project
)

router = APIRouter(prefix="/api/project", tags=["selections"])
_public_router = APIRouter(tags=["selections-public"])


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_project(project_id: int, user, db: Session) -> Project:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if not proj:
        raise HTTPException(404)
    require_org_member(db, proj.org_id, user.id, FINANCE_READ_ROLES)
    return proj


# ── Categories ─────────────────────────────────────────────────────────────────

@router.get("/{project_id}/selections/categories")
def list_categories(project_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    cats = db.query(ClientSelectionCategory).filter(
        ClientSelectionCategory.project_id == project_id
    ).order_by(ClientSelectionCategory.display_order, ClientSelectionCategory.name).all()
    return [{"id": c.id, "name": c.name, "display_order": c.display_order,
             "item_count": len(c.items),
             "confirmed_count": sum(1 for i in c.items if i.status in ("confirmed","ordered","installed")),
             "total_allowance": sum(i.allowance_amount or 0 for i in c.items),
             "total_upgrades": sum(i.upgrade_amount or 0 for i in c.items)} for c in cats]


@router.post("/{project_id}/selections/categories")
def create_category(project_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    c = ClientSelectionCategory(org_id=proj.org_id, project_id=project_id,
                                name=body["name"], display_order=body.get("display_order", 100))
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id, "ok": True}


@router.delete("/{project_id}/selections/categories/{cat_id}")
def delete_category(project_id: int, cat_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    c = db.query(ClientSelectionCategory).filter(ClientSelectionCategory.id == cat_id, ClientSelectionCategory.project_id == project_id).first()
    if c: db.delete(c); db.commit()
    return {"ok": True}


# ── Selection Items ────────────────────────────────────────────────────────────

@router.get("/{project_id}/selections")
def list_selections(project_id: int, category_id: int = None, db: Session = Depends(_db), user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    q = db.query(ClientSelection).filter(ClientSelection.project_id == project_id)
    if category_id:
        q = q.filter(ClientSelection.category_id == category_id)
    items = q.order_by(ClientSelection.category_id, ClientSelection.item_name).all()
    return [_item_out(i) for i in items]


def _item_out(i):
    return {
        "id": i.id, "category_id": i.category_id, "item_name": i.item_name,
        "description": i.description, "standard_option": i.standard_option,
        "client_choice": i.client_choice, "allowance_amount": i.allowance_amount,
        "actual_cost": i.actual_cost, "upgrade_amount": i.upgrade_amount,
        "status": i.status, "due_date": i.due_date, "notes": i.notes,
        "client_approved_at": i.client_approved_at.isoformat() if i.client_approved_at else None,
        "created_at": i.created_at.isoformat(),
    }


@router.post("/{project_id}/selections")
def create_selection(project_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    s = ClientSelection(
        org_id=proj.org_id, project_id=project_id,
        category_id=body.get("category_id"),
        item_name=body["item_name"],
        description=body.get("description"),
        standard_option=body.get("standard_option"),
        client_choice=body.get("client_choice"),
        allowance_amount=body.get("allowance_amount"),
        actual_cost=body.get("actual_cost"),
        upgrade_amount=body.get("upgrade_amount"),
        status=body.get("status", "pending"),
        due_date=body.get("due_date"),
        notes=body.get("notes"),
        created_by=user.id,
    )
    db.add(s); db.commit(); db.refresh(s)
    return {"id": s.id, "ok": True}


@router.put("/{project_id}/selections/{sel_id}")
def update_selection(project_id: int, sel_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    s = db.query(ClientSelection).filter(ClientSelection.id == sel_id, ClientSelection.project_id == project_id).first()
    if not s: raise HTTPException(404)
    for f in ["item_name","description","standard_option","client_choice","allowance_amount",
              "actual_cost","upgrade_amount","status","due_date","notes","category_id"]:
        if f in body: setattr(s, f, body[f])
    if body.get("status") == "confirmed" and not s.client_approved_at:
        s.client_approved_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/selections/{sel_id}")
def delete_selection(project_id: int, sel_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    s = db.query(ClientSelection).filter(ClientSelection.id == sel_id, ClientSelection.project_id == project_id).first()
    if s: db.delete(s); db.commit()
    return {"ok": True}


# ── Summary ────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/selections/summary")
def selections_summary(project_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    items = db.query(ClientSelection).filter(ClientSelection.project_id == project_id).all()
    total_allowance = sum(i.allowance_amount or 0 for i in items)
    total_upgrades = sum(i.upgrade_amount or 0 for i in items)
    return {
        "total_items": len(items),
        "pending": sum(1 for i in items if i.status == "pending"),
        "selected": sum(1 for i in items if i.status == "selected"),
        "confirmed": sum(1 for i in items if i.status in ("confirmed","ordered","installed")),
        "total_allowance": total_allowance,
        "total_upgrades": total_upgrades,
        "net_position": total_upgrades,
    }


# ── Client Token (public portal) ───────────────────────────────────────────────

@router.get("/{project_id}/selections/tokens")
def list_sel_tokens(project_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    _get_project(project_id, user, db)
    toks = db.query(ClientSelectionToken).filter(ClientSelectionToken.project_id == project_id).all()
    return [{"id": t.id, "token": t.token, "client_name": t.client_name,
             "client_email": t.client_email, "is_active": t.is_active,
             "portal_url": f"/selections/{t.token}"} for t in toks]


@router.post("/{project_id}/selections/tokens")
def create_sel_token(project_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    proj = _get_project(project_id, user, db)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    tok = ClientSelectionToken(
        org_id=proj.org_id, project_id=project_id,
        token=secrets.token_urlsafe(24),
        client_name=body.get("client_name"),
        client_email=body.get("client_email"),
        created_by=user.id,
    )
    db.add(tok); db.commit()
    return {"token": tok.token, "portal_url": f"/selections/{tok.token}", "ok": True}


# ── Public Client Portal ───────────────────────────────────────────────────────

@_public_router.get("/selections/{token}/api")
def sel_portal_api(token: str, db: Session = Depends(get_db)):
    tok = db.query(ClientSelectionToken).filter(
        ClientSelectionToken.token == token, ClientSelectionToken.is_active == True
    ).first()
    if not tok: raise HTTPException(404, "Portal link not found or expired.")
    proj = db.query(Project).filter(Project.id == tok.project_id).first()
    cats = db.query(ClientSelectionCategory).filter(
        ClientSelectionCategory.project_id == tok.project_id
    ).order_by(ClientSelectionCategory.display_order).all()
    items = db.query(ClientSelection).filter(ClientSelection.project_id == tok.project_id).all()
    cat_items = {}
    for i in items:
        cat_id = i.category_id or 0
        if cat_id not in cat_items:
            cat_items[cat_id] = []
        cat_items[cat_id].append(_item_out(i))

    return {
        "project_name": proj.name if proj else "",
        "client_name": tok.client_name,
        "categories": [{"id": c.id, "name": c.name, "items": cat_items.get(c.id, [])} for c in cats],
        "uncategorized": cat_items.get(0, []),
    }


@_public_router.put("/selections/{token}/api/items/{item_id}")
def client_select_item(token: str, item_id: int, body: dict, db: Session = Depends(get_db)):
    tok = db.query(ClientSelectionToken).filter(
        ClientSelectionToken.token == token, ClientSelectionToken.is_active == True
    ).first()
    if not tok: raise HTTPException(404)
    s = db.query(ClientSelection).filter(
        ClientSelection.id == item_id, ClientSelection.project_id == tok.project_id
    ).first()
    if not s: raise HTTPException(404)
    s.client_choice = body.get("client_choice", s.client_choice)
    if body.get("approved"):
        s.status = "confirmed"
        s.client_approved_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
