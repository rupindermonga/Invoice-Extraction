"""Client Change Order Approval — email token-based client e-approval workflow."""
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..dependencies import get_current_user, require_org_member, FINANCE_WRITE_ROLES
from ..models import ChangeOrder, Project, COApprovalToken

router = APIRouter(prefix="/api/project", tags=["co-approval"])
_public_router = APIRouter(tags=["co-approval-public"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Internal: create approval link ─────────────────────────────────────────────

@router.post("/{project_id}/change-orders/{co_id}/approval-link")
def create_approval_link(project_id: int, co_id: int, body: dict,
                         db: Session = Depends(get_db), user=Depends(get_current_user)):
    proj = db.query(Project).filter(Project.id == project_id).first()
    if not proj:
        raise HTTPException(404)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    co = db.query(ChangeOrder).filter(ChangeOrder.id == co_id, ChangeOrder.project_id == project_id).first()
    if not co:
        raise HTTPException(404, "Change order not found")

    existing = db.query(COApprovalToken).filter(
        COApprovalToken.co_id == co_id,
        COApprovalToken.approved_at == None,
        COApprovalToken.rejected_at == None,
    ).first()
    if existing:
        token = existing.token
    else:
        token = secrets.token_urlsafe(24)
        tok = COApprovalToken(
            org_id=proj.org_id, project_id=project_id, co_id=co_id,
            token=token,
            client_name=body.get("client_name"),
            client_email=body.get("client_email"),
            expires_at=datetime.utcnow() + timedelta(days=30),
            created_by=user.id,
        )
        db.add(tok)
        db.commit()

    return {
        "approval_url": f"/co-approval/{token}",
        "token": token,
    }


@router.get("/{project_id}/change-orders/{co_id}/approval-status")
def approval_status(project_id: int, co_id: int, db: Session = Depends(get_db),
                    user=Depends(get_current_user)):
    proj = db.query(Project).filter(Project.id == project_id).first()
    if not proj:
        raise HTTPException(404)
    require_org_member(db, proj.org_id, user.id, FINANCE_WRITE_ROLES)
    tokens = db.query(COApprovalToken).filter(COApprovalToken.co_id == co_id).order_by(COApprovalToken.created_at.desc()).all()
    return [{"id": t.id, "client_name": t.client_name, "client_email": t.client_email,
             "approved_at": t.approved_at.isoformat() if t.approved_at else None,
             "rejected_at": t.rejected_at.isoformat() if t.rejected_at else None,
             "rejection_reason": t.rejection_reason,
             "expires_at": t.expires_at.isoformat() if t.expires_at else None,
             "created_at": t.created_at.isoformat()} for t in tokens]


# ── Public: client approval portal ─────────────────────────────────────────────

@_public_router.get("/co-approval/{token}", response_class=HTMLResponse)
def co_approval_page(token: str, db: Session = Depends(get_db)):
    tok = db.query(COApprovalToken).filter(COApprovalToken.token == token).first()
    if not tok:
        return HTMLResponse(_error_page("Approval link not found or expired."), status_code=404)
    if tok.expires_at and tok.expires_at < datetime.utcnow():
        return HTMLResponse(_error_page("This approval link has expired. Contact your project team."), status_code=410)

    co = db.query(ChangeOrder).filter(ChangeOrder.id == tok.co_id).first()
    proj = db.query(Project).filter(Project.id == tok.project_id).first()

    status_html = ""
    if tok.approved_at:
        status_html = f'<div class="bg-green-50 border border-green-200 rounded-xl p-4 text-center text-green-800 font-semibold"><i class="fa-solid fa-circle-check mr-2"></i>Approved on {tok.approved_at.strftime("%B %d, %Y")}</div>'
    elif tok.rejected_at:
        status_html = f'<div class="bg-red-50 border border-red-200 rounded-xl p-4 text-center text-red-800 font-semibold"><i class="fa-solid fa-circle-xmark mr-2"></i>Rejected on {tok.rejected_at.strftime("%B %d, %Y")}<br><span class="text-sm font-normal">{tok.rejection_reason or ""}</span></div>'

    action_buttons = ""
    if not tok.approved_at and not tok.rejected_at:
        action_buttons = f"""
        <div class="flex gap-3 mt-6">
          <button onclick="submitDecision('approve')" class="flex-1 py-3 bg-green-600 text-white rounded-xl font-bold text-lg hover:bg-green-700 transition">
            <i class="fa-solid fa-circle-check mr-2"></i>Approve Change Order
          </button>
          <button onclick="document.getElementById('rejectPanel').classList.toggle('hidden')" class="flex-1 py-3 bg-red-500 text-white rounded-xl font-bold text-lg hover:bg-red-600 transition">
            <i class="fa-solid fa-circle-xmark mr-2"></i>Reject
          </button>
        </div>
        <div id="rejectPanel" class="hidden mt-4">
          <textarea id="rejectReason" rows="3" placeholder="Reason for rejection..." class="w-full border rounded-xl p-3 text-sm focus:outline-none focus:ring-2 focus:ring-red-400"></textarea>
          <button onclick="submitDecision('reject')" class="mt-2 w-full py-2 bg-red-600 text-white rounded-xl font-semibold hover:bg-red-700 transition">Confirm Rejection</button>
        </div>
        <script>
        async function submitDecision(decision) {{
          const reason = document.getElementById('rejectReason')?.value || '';
          const body = {{decision, rejection_reason: reason}};
          const r = await fetch(window.location.href.replace('/co-approval/', '/co-approval/api/'), {{
            method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(body)
          }});
          const data = await r.json();
          if (r.ok) {{ location.reload(); }} else {{ alert(data.detail || 'Error'); }}
        }}
        </script>"""

    amount_sign = "+" if (co.amount or 0) >= 0 else ""
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Change Order Approval — {proj.name if proj else ''}</title>
<link rel="stylesheet" href="/static/css/fontawesome/all.min.css"/>
<script src="/static/js/tailwind.min.js"></script>
</head><body class="bg-gray-50 min-h-screen font-sans">
<div class="max-w-xl mx-auto px-4 py-12">
  <div class="text-center mb-8">
    <img src="/static/favicon.svg" alt="Finel AI" class="w-12 h-12 mx-auto rounded-xl mb-3"/>
    <h1 class="text-2xl font-bold text-gray-800">Change Order Approval</h1>
    <p class="text-gray-500 mt-1">{proj.name if proj else ''}</p>
  </div>
  <div class="bg-white rounded-2xl border p-6 mb-4">
    <div class="flex items-center justify-between mb-4">
      <span class="text-sm text-gray-500">Change Order</span>
      <span class="font-bold text-gray-800">{co.co_number if co else ''}</span>
    </div>
    <h2 class="text-xl font-bold text-gray-800 mb-2">{co.description if co else ''}</h2>
    <div class="text-3xl font-bold {'text-green-600' if (co.amount or 0) >= 0 else 'text-red-600'} mb-4">
      {amount_sign}${abs(co.amount or 0):,.2f} CAD
    </div>
    <div class="text-sm text-gray-500 mb-1">Issued by: {co.issued_by or '—'}</div>
    <div class="text-sm text-gray-500">Date: {co.date or '—'}</div>
    {"<div class='mt-3 text-sm text-gray-600 bg-gray-50 rounded-lg p-3'>"+co.notes+"</div>" if co and co.notes else ""}
  </div>
  {status_html}
  {action_buttons}
  <p class="text-center text-xs text-gray-400 mt-6">
    Powered by <a href="https://projects.finel.ai" class="text-blue-500">Finel AI Projects</a> —
    This approval is legally binding. Contact your project team with questions.
  </p>
</div>
</body></html>"""
    return HTMLResponse(html)


@_public_router.post("/co-approval/api/{token}")
def co_approval_action(token: str, body: dict, db: Session = Depends(get_db)):
    tok = db.query(COApprovalToken).filter(COApprovalToken.token == token).first()
    if not tok:
        raise HTTPException(404)
    if tok.approved_at or tok.rejected_at:
        raise HTTPException(400, "This change order has already been decided.")
    if tok.expires_at and tok.expires_at < datetime.utcnow():
        raise HTTPException(410, "This approval link has expired.")

    decision = body.get("decision")
    if decision == "approve":
        tok.approved_at = datetime.utcnow()
        co = db.query(ChangeOrder).filter(ChangeOrder.id == tok.co_id).first()
        if co:
            co.status = "approved"
    elif decision == "reject":
        tok.rejected_at = datetime.utcnow()
        tok.rejection_reason = body.get("rejection_reason", "")
        co = db.query(ChangeOrder).filter(ChangeOrder.id == tok.co_id).first()
        if co:
            co.status = "rejected"
    else:
        raise HTTPException(400, "Decision must be 'approve' or 'reject'")

    db.commit()
    return {"ok": True, "decision": decision}


def _error_page(msg: str) -> str:
    return f"""<!DOCTYPE html><html><head><title>Error</title></head>
<body style="font-family:Arial;text-align:center;padding:60px">
<h2 style="color:#ef4444">⚠ {msg}</h2>
<p>Contact your project team for assistance.</p>
</body></html>"""
