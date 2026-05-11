"""CRM Lead Pipeline + Proposal Packages with E-Signature."""
import secrets
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..dependencies import get_current_user, get_current_org, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import CRMLead, ProposalPackage, Organization

router = APIRouter(prefix="/api/crm", tags=["crm"])
_public_router = APIRouter(tags=["proposal-public"])

STAGE_ORDER = {"prospect": 1, "qualified": 2, "proposal": 3, "won": 4, "lost": 5, "on_hold": 6}


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Leads ──────────────────────────────────────────────────────────────────────

@router.get("/leads")
def list_leads(status: str = None, org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_READ_ROLES: raise HTTPException(403)
    q = db.query(CRMLead).filter(CRMLead.org_id == org.id)
    if status: q = q.filter(CRMLead.status == status)
    leads = q.order_by(CRMLead.updated_at.desc()).all()
    return [_lead_out(l) for l in leads]


def _lead_out(l):
    return {
        "id": l.id, "company_name": l.company_name, "contact_name": l.contact_name,
        "contact_email": l.contact_email, "contact_phone": l.contact_phone,
        "project_type": l.project_type, "estimated_value": l.estimated_value,
        "location": l.location, "status": l.status, "source": l.source,
        "probability_pct": l.probability_pct, "expected_close_date": l.expected_close_date,
        "next_action": l.next_action, "next_action_date": l.next_action_date,
        "notes": l.notes, "converted_project_id": l.converted_project_id,
        "weighted_value": round((l.estimated_value or 0) * (l.probability_pct or 0) / 100, 2),
        "proposal_count": len(l.proposals),
        "created_at": l.created_at.isoformat(), "updated_at": l.updated_at.isoformat(),
    }


@router.post("/leads")
def create_lead(body: dict, org_ctx=Depends(get_current_org), db: Session = Depends(_db), user=Depends(get_current_user)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    l = CRMLead(
        org_id=org.id, company_name=body["company_name"],
        contact_name=body.get("contact_name"), contact_email=body.get("contact_email"),
        contact_phone=body.get("contact_phone"), project_type=body.get("project_type"),
        estimated_value=body.get("estimated_value"), location=body.get("location"),
        status=body.get("status", "prospect"), source=body.get("source", "referral"),
        probability_pct=body.get("probability_pct", 25),
        expected_close_date=body.get("expected_close_date"),
        notes=body.get("notes"), next_action=body.get("next_action"),
        next_action_date=body.get("next_action_date"),
        created_by=user.id,
    )
    db.add(l); db.commit(); db.refresh(l)
    return {"id": l.id, "ok": True}


@router.put("/leads/{lead_id}")
def update_lead(lead_id: int, body: dict, org_ctx=Depends(get_current_org), db: Session = Depends(_db), user=Depends(get_current_user)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    l = db.query(CRMLead).filter(CRMLead.id == lead_id, CRMLead.org_id == org.id).first()
    if not l: raise HTTPException(404)
    for f in ["company_name","contact_name","contact_email","contact_phone","project_type","estimated_value","location","status","source","probability_pct","expected_close_date","notes","next_action","next_action_date","converted_project_id"]:
        if f in body: setattr(l, f, body[f])
    l.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.delete("/leads/{lead_id}")
def delete_lead(lead_id: int, org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    l = db.query(CRMLead).filter(CRMLead.id == lead_id, CRMLead.org_id == org.id).first()
    if l: db.delete(l); db.commit()
    return {"ok": True}


@router.get("/pipeline")
def pipeline_summary(org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    """Kanban-style pipeline summary."""
    org, mem = org_ctx
    if mem.role not in FINANCE_READ_ROLES: raise HTTPException(403)
    leads = db.query(CRMLead).filter(CRMLead.org_id == org.id).all()
    stages = {}
    for stage in ["prospect","qualified","proposal","won","lost","on_hold"]:
        stage_leads = [l for l in leads if l.status == stage]
        stages[stage] = {
            "count": len(stage_leads),
            "total_value": sum(l.estimated_value or 0 for l in stage_leads),
            "weighted_value": sum((l.estimated_value or 0) * (l.probability_pct or 0) / 100 for l in stage_leads),
        }
    return {
        "stages": stages,
        "total_pipeline": sum(s["total_value"] for s in stages.values()),
        "weighted_pipeline": sum(s["weighted_value"] for s in stages.values()),
        "total_leads": len(leads),
    }


# ── Proposals ──────────────────────────────────────────────────────────────────

@router.get("/leads/{lead_id}/proposals")
def list_proposals(lead_id: int, org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_READ_ROLES: raise HTTPException(403)
    proposals = db.query(ProposalPackage).filter(ProposalPackage.lead_id == lead_id, ProposalPackage.org_id == org.id).all()
    return [_prop_out(p) for p in proposals]


@router.get("/proposals")
def list_all_proposals(org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_READ_ROLES: raise HTTPException(403)
    proposals = db.query(ProposalPackage).filter(ProposalPackage.org_id == org.id).order_by(ProposalPackage.created_at.desc()).all()
    return [_prop_out(p) for p in proposals]


def _prop_out(p):
    return {
        "id": p.id, "lead_id": p.lead_id, "proposal_number": p.proposal_number,
        "title": p.title, "client_name": p.client_name, "client_email": p.client_email,
        "total_amount": p.total_amount, "valid_until": p.valid_until,
        "status": p.status, "signed_at": p.signed_at.isoformat() if p.signed_at else None,
        "signed_by_name": p.signed_by_name,
        "sign_url": f"/proposal/{p.sign_token}" if p.sign_token and p.status != "accepted" else None,
        "created_at": p.created_at.isoformat(),
    }


@router.post("/leads/{lead_id}/proposals")
def create_proposal(lead_id: int, body: dict, org_ctx=Depends(get_current_org),
                    db: Session = Depends(_db), user=Depends(get_current_user)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    lead = db.query(CRMLead).filter(CRMLead.id == lead_id, CRMLead.org_id == org.id).first()
    if not lead: raise HTTPException(404)
    last = db.query(ProposalPackage).filter(ProposalPackage.org_id == org.id).order_by(ProposalPackage.id.desc()).first()
    num = f"PROP-{((int(last.proposal_number.split('-')[1]) if last and last.proposal_number else 0) + 1):03d}"
    p = ProposalPackage(
        org_id=org.id, lead_id=lead_id, proposal_number=num,
        title=body.get("title", f"Proposal — {lead.company_name}"),
        client_name=body.get("client_name", lead.contact_name),
        client_email=body.get("client_email", lead.contact_email),
        client_address=body.get("client_address"),
        valid_until=body.get("valid_until"),
        total_amount=body.get("total_amount", lead.estimated_value),
        scope_of_work=body.get("scope_of_work"),
        inclusions=body.get("inclusions"),
        exclusions=body.get("exclusions"),
        payment_terms=body.get("payment_terms"),
        warranty_period=body.get("warranty_period"),
        notes=body.get("notes"), status="draft",
        sign_token=secrets.token_urlsafe(24),
        created_by=user.id,
    )
    db.add(p); db.commit(); db.refresh(p)
    return {"id": p.id, "proposal_number": p.proposal_number,
            "sign_url": f"/proposal/{p.sign_token}", "ok": True}


@router.put("/proposals/{prop_id}")
def update_proposal(prop_id: int, body: dict, org_ctx=Depends(get_current_org),
                    db: Session = Depends(_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    p = db.query(ProposalPackage).filter(ProposalPackage.id == prop_id, ProposalPackage.org_id == org.id).first()
    if not p: raise HTTPException(404)
    for f in ["title","client_name","client_email","client_address","valid_until","total_amount","scope_of_work","inclusions","exclusions","payment_terms","warranty_period","notes","status"]:
        if f in body: setattr(p, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/proposals/{prop_id}")
def delete_proposal(prop_id: int, org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    p = db.query(ProposalPackage).filter(ProposalPackage.id == prop_id, ProposalPackage.org_id == org.id).first()
    if p: db.delete(p); db.commit()
    return {"ok": True}


# ── Public Proposal Portal ─────────────────────────────────────────────────────

@_public_router.get("/proposal/{token}", response_class=HTMLResponse)
def proposal_portal(token: str, db: Session = Depends(get_db)):
    p = db.query(ProposalPackage).filter(ProposalPackage.sign_token == token).first()
    if not p: return HTMLResponse("<html><body><h2>Proposal not found.</h2></body></html>", 404)
    org = db.query(Organization).filter(Organization.id == p.org_id).first()
    today = datetime.utcnow().strftime("%B %d, %Y")
    status_html = ""
    if p.signed_at:
        status_html = f'<div style="background:#d1fae5;border:1px solid #6ee7b7;border-radius:12px;padding:16px;text-align:center;color:#065f46;font-weight:bold;margin-bottom:20px"><i class="fa-solid fa-signature"></i> Accepted by {p.signed_by_name} on {p.signed_at.strftime("%B %d, %Y")}</div>'
    sign_section = "" if p.signed_at or p.status in ("rejected","expired") else """
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin-top:24px">
      <h3 style="font-size:15px;margin-bottom:12px">Accept This Proposal</h3>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
        <div><label style="font-size:11px;color:#6b7280;display:block;margin-bottom:4px">Full Name *</label><input id="sigName" style="width:100%;border:1px solid #d1d5db;border-radius:8px;padding:8px 12px;font-size:14px" /></div>
        <div><label style="font-size:11px;color:#6b7280;display:block;margin-bottom:4px">Title</label><input id="sigTitle" style="width:100%;border:1px solid #d1d5db;border-radius:8px;padding:8px 12px;font-size:14px" /></div>
      </div>
      <p style="font-size:11px;color:#9ca3af;margin-bottom:12px">By clicking Accept, you agree to the terms of this proposal. This constitutes your electronic signature.</p>
      <button onclick="acceptProposal()" style="width:100%;padding:12px;background:#2563eb;color:#fff;border:none;border-radius:10px;font-size:15px;font-weight:bold;cursor:pointer">Accept Proposal</button>
      <div id="sigError" style="color:#ef4444;font-size:13px;text-align:center;margin-top:8px;display:none"></div>
    </div>
    <script>
    async function acceptProposal(){
      const name=document.getElementById('sigName').value.trim();
      if(!name){document.getElementById('sigError').textContent='Name required';document.getElementById('sigError').style.display='block';return;}
      const title=document.getElementById('sigTitle').value.trim();
      const r=await fetch(window.location.pathname+'/accept',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({signed_by_name:name+(title?' ('+title+')':'')})});
      if(r.ok){location.reload();}else{const e=await r.json();document.getElementById('sigError').textContent=e.detail||'Error';document.getElementById('sigError').style.display='block';}
    }
    </script>"""
    return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Proposal — {p.title}</title>
<link rel="stylesheet" href="/static/css/fontawesome/all.min.css"/>
<style>body{{font-family:Arial,sans-serif;background:#f8fafc;margin:0;padding:0}}
.container{{max-width:720px;margin:40px auto;padding:0 16px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:32px;margin-bottom:20px}}
h1{{font-size:24px;color:#1e293b;margin-bottom:4px}}
h2{{font-size:16px;color:#475569;font-weight:500}}
h3{{font-size:15px;border-bottom:2px solid #e2e8f0;padding-bottom:8px;margin-top:24px}}
.meta{{display:flex;gap:24px;margin:16px 0;flex-wrap:wrap}}
.meta div{{font-size:13px;color:#64748b}}
.meta strong{{display:block;font-size:18px;color:#1e293b;font-weight:bold}}
</style>
</head><body>
<div class="container">
  <div style="text-align:center;margin-bottom:20px">
    <img src="/static/favicon.svg" style="width:48px;height:48px;border-radius:12px;margin-bottom:8px" alt="Finel AI"/>
    <p style="font-size:13px;color:#94a3b8">{org.name if org else ''}</p>
  </div>
  {status_html}
  <div class="card">
    <h1>{p.title}</h1>
    <h2>{p.proposal_number} · Prepared {today}</h2>
    <div class="meta">
      <div><span>Prepared for</span><strong>{p.client_name or '—'}</strong></div>
      {f'<div><span>Total Investment</span><strong>${p.total_amount:,.0f} CAD</strong></div>' if p.total_amount else ''}
      {f'<div><span>Valid Until</span><strong>{p.valid_until}</strong></div>' if p.valid_until else ''}
    </div>
    {f'<h3>Scope of Work</h3><p style="font-size:14px;line-height:1.7;white-space:pre-wrap">{p.scope_of_work}</p>' if p.scope_of_work else ''}
    {f'<h3>Inclusions</h3><p style="font-size:14px;white-space:pre-wrap">{p.inclusions}</p>' if p.inclusions else ''}
    {f'<h3>Exclusions</h3><p style="font-size:14px;white-space:pre-wrap">{p.exclusions}</p>' if p.exclusions else ''}
    {f'<h3>Payment Terms</h3><p style="font-size:14px;white-space:pre-wrap">{p.payment_terms}</p>' if p.payment_terms else ''}
    {f'<h3>Warranty</h3><p style="font-size:14px">{p.warranty_period}</p>' if p.warranty_period else ''}
  </div>
  {sign_section}
  <p style="text-align:center;font-size:12px;color:#94a3b8;margin-top:20px">Powered by <a href="https://projects.finel.ai" style="color:#3b82f6">Finel AI Projects</a></p>
</div>
</body></html>""")


@_public_router.post("/proposal/{token}/accept")
def accept_proposal(token: str, body: dict, db: Session = Depends(get_db)):
    p = db.query(ProposalPackage).filter(ProposalPackage.sign_token == token).first()
    if not p: raise HTTPException(404)
    if p.signed_at: raise HTTPException(400, "Already accepted.")
    p.signed_at = datetime.utcnow()
    p.signed_by_name = body.get("signed_by_name", "")
    p.status = "accepted"
    db.commit()
    return {"ok": True}
