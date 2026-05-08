"""Audit log — read-only endpoint for org admins + a write helper used by other routes."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from typing import Tuple

from ..database import get_db
from ..models import AuditLog, User
from ..dependencies import get_current_org, require_org_role

router = APIRouter(prefix="/api/audit", tags=["audit"])


# ── Public write helper (imported by other routes) ────────────────────────────

def log(db: Session, org_id: int, user: User | None,
        action: str, entity_type: str = None, entity_id: int = None,
        detail: str = None, request: Request = None) -> None:
    """Best-effort audit write — never raises."""
    try:
        ip = None
        if request:
            forwarded = request.headers.get("X-Forwarded-For")
            ip = forwarded.split(",")[0].strip() if forwarded else (
                request.client.host if request.client else None)
        db.add(AuditLog(
            org_id=org_id,
            user_id=user.id if user else None,
            username=user.username if user else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
            ip_address=ip,
        ))
        db.commit()
    except Exception:
        db.rollback()


# ── Read endpoint ─────────────────────────────────────────────────────────────

@router.get("")
def get_audit_log(
    page: int = 1,
    limit: int = 50,
    action: str = None,
    org_ctx: Tuple = Depends(require_org_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Paginated audit log for the current org. Owner/admin only."""
    org, _ = org_ctx
    q = db.query(AuditLog).filter(AuditLog.org_id == org.id)
    if action:
        q = q.filter(AuditLog.action == action)
    total = q.count()
    rows = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "total": total, "page": page, "limit": limit,
        "items": [
            {"id": r.id, "action": r.action, "entity_type": r.entity_type,
             "entity_id": r.entity_id, "detail": r.detail,
             "username": r.username, "ip_address": r.ip_address,
             "created_at": str(r.created_at)}
            for r in rows
        ],
    }
