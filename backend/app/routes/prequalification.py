"""Subcontractor Prequalification — self-serve portal for subs to submit qualifications."""
import secrets, json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..dependencies import get_current_user, get_current_org, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import SubPrequalification, Organization

router = APIRouter(prefix="/api/prequal", tags=["prequalification"])
_public_router = APIRouter(tags=["prequal-public"])


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/")
def list_prequals(org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    org, _ = org_ctx
    rows = db.query(SubPrequalification).filter(SubPrequalification.org_id == org.id).order_by(SubPrequalification.created_at.desc()).all()
    return [_out(r) for r in rows]


def _out(r):
    return {
        "id": r.id, "company_name": r.company_name, "trade": r.trade,
        "contact_name": r.contact_name, "contact_email": r.contact_email,
        "years_in_business": r.years_in_business, "annual_revenue": r.annual_revenue,
        "bonding_capacity": r.bonding_capacity, "status": r.status,
        "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
        "invite_token": r.invite_token,
        "portal_url": f"/prequal/{r.invite_token}" if r.invite_token else None,
        "notes": r.notes, "created_at": r.created_at.isoformat(),
    }


@router.post("/invite")
def create_invite(body: dict, org_ctx=Depends(get_current_org), db: Session = Depends(_db),
                  user=Depends(get_current_user)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    p = SubPrequalification(
        org_id=org.id,
        company_name=body.get("company_name", "Pending"),
        trade=body.get("trade"),
        contact_email=body.get("contact_email"),
        status="invited",
        invite_token=secrets.token_urlsafe(24),
    )
    db.add(p); db.commit(); db.refresh(p)
    return {"id": p.id, "invite_token": p.invite_token, "portal_url": f"/prequal/{p.invite_token}"}


@router.put("/{prequal_id}/status")
def update_status(prequal_id: int, body: dict, org_ctx=Depends(get_current_org),
                  db: Session = Depends(_db), user=Depends(get_current_user)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    p = db.query(SubPrequalification).filter(SubPrequalification.id == prequal_id, SubPrequalification.org_id == org.id).first()
    if not p: raise HTTPException(404)
    p.status = body.get("status", p.status)
    p.notes = body.get("notes", p.notes)
    if body.get("status") in ("approved","rejected"):
        p.reviewed_by = user.id
        p.reviewed_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.delete("/{prequal_id}")
def delete_prequal(prequal_id: int, org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    p = db.query(SubPrequalification).filter(SubPrequalification.id == prequal_id, SubPrequalification.org_id == org.id).first()
    if p: db.delete(p); db.commit()
    return {"ok": True}


# ── Public Prequalification Portal ─────────────────────────────────────────────

@_public_router.get("/prequal/{token}/api")
def prequal_portal_get(token: str, db: Session = Depends(get_db)):
    p = db.query(SubPrequalification).filter(SubPrequalification.invite_token == token).first()
    if not p: raise HTTPException(404, "Prequalification link not found.")
    org = db.query(Organization).filter(Organization.id == p.org_id).first()
    return {
        "org_name": org.name if org else "",
        "company_name": p.company_name,
        "trade": p.trade,
        "status": p.status,
        "submitted": p.submitted_at is not None,
    }


@_public_router.put("/prequal/{token}/api")
def prequal_portal_submit(token: str, body: dict, db: Session = Depends(get_db)):
    p = db.query(SubPrequalification).filter(SubPrequalification.invite_token == token).first()
    if not p: raise HTTPException(404)
    if p.submitted_at: raise HTTPException(400, "Already submitted.")
    p.company_name = body.get("company_name", p.company_name)
    p.trade = body.get("trade", p.trade)
    p.contact_name = body.get("contact_name")
    p.contact_email = body.get("contact_email", p.contact_email)
    p.years_in_business = body.get("years_in_business")
    p.annual_revenue = body.get("annual_revenue")
    p.bonding_capacity = body.get("bonding_capacity")
    p.largest_project = body.get("largest_project")
    p.safety_record = body.get("safety_record")
    p.wsib_number = body.get("wsib_number")
    p.cra_bn = body.get("cra_bn")
    p.hst_number = body.get("hst_number")
    p.references = json.dumps(body.get("references", []))
    p.status = "submitted"
    p.submitted_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "message": "Your prequalification has been submitted successfully."}


@_public_router.get("/prequal/{token}", response_class=HTMLResponse)
def prequal_portal_page(token: str, db: Session = Depends(get_db)):
    p = db.query(SubPrequalification).filter(SubPrequalification.invite_token == token).first()
    if not p:
        return HTMLResponse("<html><body><h2>Link not found.</h2></body></html>", 404)
    org = db.query(Organization).filter(Organization.id == p.org_id).first()
    submitted_html = ""
    if p.submitted_at:
        submitted_html = '<div class="bg-green-50 border border-green-200 rounded-xl p-5 text-center text-green-700 font-semibold"><i class="fa-solid fa-circle-check mr-2"></i>Your prequalification was submitted successfully. The team will review and contact you.</div>'
    form_html = "" if p.submitted_at else """
    <form id="pqForm" class="space-y-4">
      <div class="grid grid-cols-2 gap-4">
        <div><label class="block text-xs font-medium text-gray-600 mb-1">Company Name *</label><input id="company_name" required class="w-full border rounded-xl px-3 py-2 text-sm" /></div>
        <div><label class="block text-xs font-medium text-gray-600 mb-1">Trade / Division *</label><input id="trade" required class="w-full border rounded-xl px-3 py-2 text-sm" /></div>
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div><label class="block text-xs font-medium text-gray-600 mb-1">Contact Name *</label><input id="contact_name" required class="w-full border rounded-xl px-3 py-2 text-sm" /></div>
        <div><label class="block text-xs font-medium text-gray-600 mb-1">Contact Email *</label><input id="contact_email" type="email" required class="w-full border rounded-xl px-3 py-2 text-sm" /></div>
      </div>
      <div class="grid grid-cols-3 gap-4">
        <div><label class="block text-xs font-medium text-gray-600 mb-1">Years in Business</label><input id="years_in_business" type="number" class="w-full border rounded-xl px-3 py-2 text-sm" /></div>
        <div><label class="block text-xs font-medium text-gray-600 mb-1">Annual Revenue ($)</label><input id="annual_revenue" type="number" class="w-full border rounded-xl px-3 py-2 text-sm" /></div>
        <div><label class="block text-xs font-medium text-gray-600 mb-1">Bonding Capacity ($)</label><input id="bonding_capacity" type="number" class="w-full border rounded-xl px-3 py-2 text-sm" /></div>
      </div>
      <div class="grid grid-cols-3 gap-4">
        <div><label class="block text-xs font-medium text-gray-600 mb-1">WSIB Number</label><input id="wsib_number" class="w-full border rounded-xl px-3 py-2 text-sm" /></div>
        <div><label class="block text-xs font-medium text-gray-600 mb-1">CRA Business Number</label><input id="cra_bn" class="w-full border rounded-xl px-3 py-2 text-sm" /></div>
        <div><label class="block text-xs font-medium text-gray-600 mb-1">HST Registration #</label><input id="hst_number" class="w-full border rounded-xl px-3 py-2 text-sm" /></div>
      </div>
      <div><label class="block text-xs font-medium text-gray-600 mb-1">Safety Record / COR Status</label><textarea id="safety_record" rows="2" class="w-full border rounded-xl px-3 py-2 text-sm"></textarea></div>
      <div><label class="block text-xs font-medium text-gray-600 mb-1">Largest Completed Project ($)</label><input id="largest_project" type="number" class="w-full border rounded-xl px-3 py-2 text-sm" /></div>
      <div id="submitError" class="hidden text-red-600 text-sm"></div>
      <button type="submit" class="w-full py-3 bg-blue-600 text-white rounded-xl font-semibold hover:bg-blue-700">Submit Prequalification</button>
    </form>
    <script>
    document.getElementById('pqForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const val = id => document.getElementById(id)?.value;
      const num = id => parseFloat(val(id)) || null;
      const body = {company_name:val('company_name'),trade:val('trade'),contact_name:val('contact_name'),contact_email:val('contact_email'),years_in_business:parseInt(val('years_in_business'))||null,annual_revenue:num('annual_revenue'),bonding_capacity:num('bonding_capacity'),largest_project:num('largest_project'),safety_record:val('safety_record'),wsib_number:val('wsib_number'),cra_bn:val('cra_bn'),hst_number:val('hst_number')};
      const r = await fetch(window.location.pathname.replace('/prequal/','/prequal/')+'/api', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      if(r.ok){location.reload();}else{const e=await r.json();document.getElementById('submitError').textContent=e.detail||'Error';document.getElementById('submitError').classList.remove('hidden');}
    });
    </script>"""
    return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Subcontractor Prequalification — {org.name if org else 'Finel AI'}</title>
<link rel="stylesheet" href="/static/css/fontawesome/all.min.css"/>
<script src="/static/js/tailwind.min.js"></script>
</head><body class="bg-gray-50 min-h-screen font-sans">
<div class="max-w-2xl mx-auto px-4 py-10">
  <div class="text-center mb-8">
    <img src="/static/favicon.svg" class="w-12 h-12 mx-auto rounded-xl mb-3" alt="Finel AI"/>
    <h1 class="text-2xl font-bold text-gray-800">Subcontractor Prequalification</h1>
    <p class="text-gray-500 mt-1">{org.name if org else ''}</p>
  </div>
  <div class="bg-white border rounded-2xl p-6">
    {submitted_html}{form_html}
  </div>
  <p class="text-center text-xs text-gray-400 mt-6">Powered by <a href="https://projects.finel.ai" class="text-blue-500">Finel AI Projects</a></p>
</div></body></html>""")
