"""Cost Catalog/Assemblies, Procurement Schedule, Value Engineering Log,
CCDC Contract Library, CCDC 9A/9B, Client Payment Schedules, Unit Releases, Specialized Checklists."""
from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..dependencies import get_current_user, get_current_org, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import (
    CostAssembly, CostAssemblyItem, ProcurementItem, ValueEngineeringItem,
    CCDCContract, StatutoryDeclaration9A9B, UnitRelease, ClientPaymentSchedule,
    SpecializedChecklistItem, Project, Estimate, EstimateLineItem,
)

router = APIRouter(prefix="/api", tags=["assemblies"])


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _proj(project_id, user, db):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p: raise HTTPException(404)
    require_org_member(db, p.org_id, user.id, FINANCE_READ_ROLES)
    return p


# ── Cost Assemblies (org-level) ─────────────────────────────────────────────────

@router.get("/assemblies")
def list_assemblies(org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    org, _ = org_ctx
    rows = db.query(CostAssembly).filter(CostAssembly.org_id == org.id).order_by(CostAssembly.trade_category, CostAssembly.name).all()
    return [{"id": r.id, "name": r.name, "description": r.description,
             "trade_category": r.trade_category, "unit": r.unit, "usage_count": r.usage_count,
             "total_cost": round(sum(i.total_cost or 0 for i in r.items), 2),
             "item_count": len(r.items)} for r in rows]


@router.post("/assemblies")
def create_assembly(body: dict, org_ctx=Depends(get_current_org), db: Session = Depends(_db), user=Depends(get_current_user)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    a = CostAssembly(org_id=org.id, name=body["name"], description=body.get("description"),
                     trade_category=body.get("trade_category"), unit=body.get("unit"), created_by=user.id)
    db.add(a); db.commit(); db.refresh(a)
    return {"id": a.id, "ok": True}


@router.get("/assemblies/{asm_id}/items")
def get_assembly(asm_id: int, org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    org, _ = org_ctx
    a = db.query(CostAssembly).filter(CostAssembly.id == asm_id, CostAssembly.org_id == org.id).first()
    if not a: raise HTTPException(404)
    return {"assembly": {"id": a.id, "name": a.name, "trade_category": a.trade_category, "unit": a.unit},
            "items": [{"id": i.id, "division": i.division, "description": i.description,
                       "quantity": i.quantity, "unit": i.unit, "unit_cost": i.unit_cost,
                       "total_cost": i.total_cost, "display_order": i.display_order} for i in sorted(a.items, key=lambda x: x.display_order)],
            "total": round(sum(i.total_cost or 0 for i in a.items), 2)}


@router.post("/assemblies/{asm_id}/items")
def add_assembly_item(asm_id: int, body: dict, org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    a = db.query(CostAssembly).filter(CostAssembly.id == asm_id, CostAssembly.org_id == org.id).first()
    if not a: raise HTTPException(404)
    qty = body.get("quantity"); unit_cost = body.get("unit_cost")
    total = body.get("total_cost") or (qty * unit_cost if qty and unit_cost else None)
    i = CostAssemblyItem(assembly_id=asm_id, division=body.get("division"),
                         description=body["description"], quantity=qty, unit=body.get("unit"),
                         unit_cost=unit_cost, total_cost=total, display_order=body.get("display_order", 100))
    db.add(i); db.commit()
    return {"id": i.id, "ok": True}


@router.delete("/assemblies/{asm_id}")
def delete_assembly(asm_id: int, org_ctx=Depends(get_current_org), db: Session = Depends(_db)):
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    a = db.query(CostAssembly).filter(CostAssembly.id == asm_id, CostAssembly.org_id == org.id).first()
    if a: db.delete(a); db.commit()
    return {"ok": True}


@router.post("/assemblies/{asm_id}/apply-to-estimate/{estimate_id}")
def apply_assembly(asm_id: int, estimate_id: int, body: dict,
                   org_ctx=Depends(get_current_org), db: Session = Depends(_db), user=Depends(get_current_user)):
    """Apply a cost assembly to an estimate with a quantity multiplier."""
    org, mem = org_ctx
    if mem.role not in FINANCE_WRITE_ROLES: raise HTTPException(403)
    a = db.query(CostAssembly).filter(CostAssembly.id == asm_id, CostAssembly.org_id == org.id).first()
    if not a: raise HTTPException(404)
    est = db.query(Estimate).filter(Estimate.id == estimate_id, Estimate.org_id == org.id).first()
    if not est: raise HTTPException(404)
    multiplier = body.get("multiplier", 1.0)
    added = 0
    for item in a.items:
        qty = (item.quantity or 1) * multiplier
        unit_cost = item.unit_cost or 0
        db.add(EstimateLineItem(
            estimate_id=estimate_id, org_id=org.id, project_id=est.project_id,
            division=item.division, description=item.description,
            quantity=qty, unit=item.unit, unit_cost=unit_cost,
            total_cost=round(qty * unit_cost, 2) if unit_cost else None,
        ))
        added += 1
    a.usage_count += 1
    db.commit()
    return {"added": added, "ok": True}


# ── Procurement Schedule ────────────────────────────────────────────────────────

@router.get("/project/{project_id}/procurement")
def list_procurement(project_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    items = db.query(ProcurementItem).filter(ProcurementItem.project_id == project_id).order_by(ProcurementItem.required_on_site_date, ProcurementItem.status).all()
    today = date.today().isoformat()
    return [{"id": i.id, "item_name": i.item_name, "vendor_name": i.vendor_name,
             "category": i.category, "lead_time_weeks": i.lead_time_weeks,
             "order_date": i.order_date, "required_on_site_date": i.required_on_site_date,
             "delivery_date": i.delivery_date, "total_cost": i.total_cost,
             "purchase_order_number": i.purchase_order_number, "status": i.status,
             "delay_reason": i.delay_reason, "notes": i.notes,
             "is_delayed": i.required_on_site_date and i.required_on_site_date < today and i.status not in ("delivered","cancelled"),
             "must_order_by": (_calc_order_by(i.required_on_site_date, i.lead_time_weeks) if i.required_on_site_date and i.lead_time_weeks else None),
             "created_at": i.created_at.isoformat()} for i in items]


def _calc_order_by(required_date: str, lead_weeks: int) -> str:
    try:
        return (datetime.strptime(required_date, "%Y-%m-%d") - timedelta(weeks=lead_weeks)).strftime("%Y-%m-%d")
    except Exception:
        return None


@router.post("/project/{project_id}/procurement")
def create_procurement(project_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    qty = body.get("quantity"); unit_cost = body.get("unit_cost")
    i = ProcurementItem(
        org_id=p.org_id, project_id=project_id,
        item_name=body["item_name"], vendor_name=body.get("vendor_name"),
        description=body.get("description"), category=body.get("category","other"),
        lead_time_weeks=body.get("lead_time_weeks"),
        order_date=body.get("order_date"), required_on_site_date=body.get("required_on_site_date"),
        delivery_date=body.get("delivery_date"), quantity=qty, unit=body.get("unit"),
        unit_cost=unit_cost, total_cost=body.get("total_cost") or (qty*unit_cost if qty and unit_cost else None),
        purchase_order_number=body.get("purchase_order_number"),
        status=body.get("status","to_order"), delay_reason=body.get("delay_reason"),
        notes=body.get("notes"), created_by=user.id,
    )
    db.add(i); db.commit(); db.refresh(i)
    return {"id": i.id, "ok": True}


@router.put("/project/{project_id}/procurement/{item_id}")
def update_procurement(project_id: int, item_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    i = db.query(ProcurementItem).filter(ProcurementItem.id == item_id, ProcurementItem.project_id == project_id).first()
    if not i: raise HTTPException(404)
    for f in ["item_name","vendor_name","description","category","lead_time_weeks","order_date","required_on_site_date","delivery_date","quantity","unit","unit_cost","total_cost","purchase_order_number","status","delay_reason","notes"]:
        if f in body: setattr(i, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/project/{project_id}/procurement/{item_id}")
def delete_procurement(project_id: int, item_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    i = db.query(ProcurementItem).filter(ProcurementItem.id == item_id, ProcurementItem.project_id == project_id).first()
    if i: db.delete(i); db.commit()
    return {"ok": True}


# ── Value Engineering Log ────────────────────────────────────────────────────────

@router.get("/project/{project_id}/ve-log")
def list_ve(project_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    _proj(project_id, user, db)
    items = db.query(ValueEngineeringItem).filter(ValueEngineeringItem.project_id == project_id).order_by(ValueEngineeringItem.created_at.desc()).all()
    total_savings = sum(i.potential_savings or 0 for i in items if i.status == "accepted")
    return {"items": [{"id": i.id, "item_number": i.item_number, "description": i.description,
                        "original_spec": i.original_spec, "proposed_alternate": i.proposed_alternate,
                        "original_cost": i.original_cost, "alternate_cost": i.alternate_cost,
                        "potential_savings": i.potential_savings,
                        "status": i.status, "accepted_by": i.accepted_by,
                        "decision_date": i.decision_date, "owner_approved": i.owner_approved,
                        "notes": i.notes, "created_at": i.created_at.isoformat()} for i in items],
            "summary": {"total": len(items), "accepted": sum(1 for i in items if i.status=="accepted"),
                        "pending": sum(1 for i in items if i.status in ("proposed","under_review")),
                        "total_savings_accepted": round(total_savings, 2)}}


@router.post("/project/{project_id}/ve-log")
def create_ve(project_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    last = db.query(ValueEngineeringItem).filter(ValueEngineeringItem.project_id == project_id).order_by(ValueEngineeringItem.id.desc()).first()
    num = f"VE-{((int(last.item_number.split('-')[1]) if last and last.item_number else 0) + 1):03d}"
    orig = body.get("original_cost"); alt = body.get("alternate_cost")
    savings = body.get("potential_savings") or (round(orig - alt, 2) if orig is not None and alt is not None else None)
    i = ValueEngineeringItem(
        org_id=p.org_id, project_id=project_id, item_number=body.get("item_number", num),
        description=body["description"], original_spec=body.get("original_spec"),
        proposed_alternate=body.get("proposed_alternate"),
        original_cost=orig, alternate_cost=alt, potential_savings=savings,
        status=body.get("status","proposed"), accepted_by=body.get("accepted_by"),
        decision_date=body.get("decision_date"), owner_approved=body.get("owner_approved",False),
        notes=body.get("notes"), created_by=user.id,
    )
    db.add(i); db.commit(); db.refresh(i)
    return {"id": i.id, "ok": True}


@router.put("/project/{project_id}/ve-log/{item_id}")
def update_ve(project_id: int, item_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    i = db.query(ValueEngineeringItem).filter(ValueEngineeringItem.id == item_id, ValueEngineeringItem.project_id == project_id).first()
    if not i: raise HTTPException(404)
    for f in ["description","original_spec","proposed_alternate","original_cost","alternate_cost","potential_savings","status","accepted_by","decision_date","owner_approved","notes"]:
        if f in body: setattr(i, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/project/{project_id}/ve-log/{item_id}")
def delete_ve(project_id: int, item_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    i = db.query(ValueEngineeringItem).filter(ValueEngineeringItem.id == item_id, ValueEngineeringItem.project_id == project_id).first()
    if i: db.delete(i); db.commit()
    return {"ok": True}


# ── CCDC Contract Library ────────────────────────────────────────────────────────

CCDC_TYPES = {
    "CCDC2": "CCDC 2 – Stipulated Price Contract",
    "CCDC4": "CCDC 4 – Unit Price Contract",
    "CCDC5A": "CCDC 5A – Construction Management (Services)",
    "CCDC5B": "CCDC 5B – Construction Management (Trade)",
    "CCDC14": "CCDC 14 – Design-Build Stipulated Price",
    "CCDC17": "CCDC 17 – Stipulated Price for Trade Contractors",
    "CCDC30": "CCDC 30 – Integrated Project Delivery",
    "CCDC40": "CCDC 40 – Rights & Responsibilities",
    "CCDC41": "CCDC 41 – Bid Bond",
}

@router.get("/project/{project_id}/ccdc-contracts")
def list_ccdc(project_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    _proj(project_id, user, db)
    contracts = db.query(CCDCContract).filter(CCDCContract.project_id == project_id).all()
    return [{"id": c.id, "ccdc_type": c.ccdc_type, "ccdc_name": CCDC_TYPES.get(c.ccdc_type, c.ccdc_type),
             "title": c.title, "contract_value": c.contract_value,
             "contractor_name": c.contractor_name, "owner_name": c.owner_name,
             "execution_date": c.execution_date, "substantial_performance_date": c.substantial_performance_date,
             "holdback_pct": c.holdback_pct, "insurance_required": c.insurance_required,
             "bond_required": c.bond_required, "status": c.status,
             "stat_decl_count": len(c.stat_decls_9a9b),
             "notes": c.notes, "created_at": c.created_at.isoformat()} for c in contracts]


@router.post("/project/{project_id}/ccdc-contracts")
def create_ccdc(project_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    c = CCDCContract(
        org_id=p.org_id, project_id=project_id,
        ccdc_type=body.get("ccdc_type","CCDC2"),
        title=body.get("title"), contract_value=body.get("contract_value"),
        contractor_name=body.get("contractor_name"), owner_name=body.get("owner_name", p.client),
        execution_date=body.get("execution_date"),
        substantial_performance_date=body.get("substantial_performance_date"),
        final_completion_date=body.get("final_completion_date"),
        holdback_pct=body.get("holdback_pct",10.0),
        insurance_required=body.get("insurance_required",True),
        bond_required=body.get("bond_required",False),
        supplementary_conditions=body.get("supplementary_conditions"),
        status=body.get("status","draft"), notes=body.get("notes"), created_by=user.id,
    )
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id, "ok": True}


@router.put("/project/{project_id}/ccdc-contracts/{ccdc_id}")
def update_ccdc(project_id: int, ccdc_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    c = db.query(CCDCContract).filter(CCDCContract.id == ccdc_id, CCDCContract.project_id == project_id).first()
    if not c: raise HTTPException(404)
    for f in ["ccdc_type","title","contract_value","contractor_name","owner_name","execution_date","substantial_performance_date","final_completion_date","holdback_pct","insurance_required","bond_required","supplementary_conditions","status","notes"]:
        if f in body: setattr(c, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/project/{project_id}/ccdc-contracts/{ccdc_id}")
def delete_ccdc(project_id: int, ccdc_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    c = db.query(CCDCContract).filter(CCDCContract.id == ccdc_id, CCDCContract.project_id == project_id).first()
    if c: db.delete(c); db.commit()
    return {"ok": True}


# ── CCDC 9A/9B Statutory Declaration ──────────────────────────────────────────

@router.get("/project/{project_id}/ccdc-contracts/{ccdc_id}/declarations")
def list_9a9b(project_id: int, ccdc_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    _proj(project_id, user, db)
    rows = db.query(StatutoryDeclaration9A9B).filter(StatutoryDeclaration9A9B.ccdc_contract_id == ccdc_id).all()
    return [{"id": r.id, "form_type": r.form_type, "declarant_name": r.declarant_name,
             "declarant_company": r.declarant_company, "declaration_date": r.declaration_date,
             "period_covered": r.period_covered, "amount_declared": r.amount_declared,
             "all_subs_paid": r.all_subs_paid, "outstanding_claims": r.outstanding_claims,
             "commissioner_name": r.commissioner_name, "commissioner_date": r.commissioner_date,
             "status": r.status, "notes": r.notes} for r in rows]


@router.post("/project/{project_id}/ccdc-contracts/{ccdc_id}/declarations")
def create_9a9b(project_id: int, ccdc_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    r = StatutoryDeclaration9A9B(
        org_id=p.org_id, project_id=project_id, ccdc_contract_id=ccdc_id,
        form_type=body.get("form_type","9A"), declarant_name=body.get("declarant_name"),
        declarant_title=body.get("declarant_title"), declarant_company=body.get("declarant_company"),
        declaration_date=body.get("declaration_date"), period_covered=body.get("period_covered"),
        amount_declared=body.get("amount_declared"), all_subs_paid=body.get("all_subs_paid"),
        outstanding_claims=body.get("outstanding_claims"),
        commissioner_name=body.get("commissioner_name"), commissioner_date=body.get("commissioner_date"),
        status=body.get("status","pending"), notes=body.get("notes"), created_by=user.id,
    )
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "ok": True}


@router.put("/project/{project_id}/ccdc-contracts/{ccdc_id}/declarations/{decl_id}")
def update_9a9b(project_id: int, ccdc_id: int, decl_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(StatutoryDeclaration9A9B).filter(StatutoryDeclaration9A9B.id == decl_id).first()
    if not r: raise HTTPException(404)
    for f in ["form_type","declarant_name","declarant_title","declarant_company","declaration_date","period_covered","amount_declared","all_subs_paid","outstanding_claims","commissioner_name","commissioner_date","status","notes"]:
        if f in body: setattr(r, f, body[f])
    db.commit()
    return {"ok": True}


# ── Unit Release Tracking ─────────────────────────────────────────────────────

@router.get("/project/{project_id}/units")
def list_units(project_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    _proj(project_id, user, db)
    units = db.query(UnitRelease).filter(UnitRelease.project_id == project_id).order_by(UnitRelease.unit_number).all()
    total = len(units); sold = sum(1 for u in units if u.status in ("sold","closed"))
    revenue = sum(u.sale_price or u.list_price or 0 for u in units if u.status in ("sold","closed"))
    deposits = sum(u.deposit_amount or 0 for u in units if u.deposit_received_date)
    return {"units": [{"id": u.id, "unit_number": u.unit_number, "unit_type": u.unit_type,
                        "floor_area_sf": u.floor_area_sf, "floor_number": u.floor_number,
                        "list_price": u.list_price, "sale_price": u.sale_price,
                        "buyer_name": u.buyer_name, "deposit_amount": u.deposit_amount,
                        "deposit_received_date": u.deposit_received_date,
                        "purchase_agreement_date": u.purchase_agreement_date,
                        "closing_date": u.closing_date, "status": u.status,
                        "incentives": u.incentives, "notes": u.notes} for u in units],
            "summary": {"total": total, "available": sum(1 for u in units if u.status=="available"),
                        "reserved": sum(1 for u in units if u.status=="reserved"),
                        "sold": sold, "closed": sum(1 for u in units if u.status=="closed"),
                        "absorption_pct": round(sold/total*100,1) if total else 0,
                        "total_revenue": round(revenue, 2), "total_deposits": round(deposits, 2)}}


@router.post("/project/{project_id}/units")
def create_unit(project_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    u = UnitRelease(org_id=p.org_id, project_id=project_id, **{k: v for k, v in body.items() if k in ["unit_number","unit_type","floor_area_sf","floor_number","list_price","sale_price","buyer_name","deposit_amount","deposit_received_date","purchase_agreement_date","closing_date","status","incentives","notes"]})
    db.add(u); db.commit(); db.refresh(u)
    return {"id": u.id, "ok": True}


@router.put("/project/{project_id}/units/{unit_id}")
def update_unit(project_id: int, unit_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    u = db.query(UnitRelease).filter(UnitRelease.id == unit_id, UnitRelease.project_id == project_id).first()
    if not u: raise HTTPException(404)
    for f in ["unit_number","unit_type","floor_area_sf","floor_number","list_price","sale_price","buyer_name","deposit_amount","deposit_received_date","purchase_agreement_date","closing_date","status","incentives","notes"]:
        if f in body: setattr(u, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/project/{project_id}/units/{unit_id}")
def delete_unit(project_id: int, unit_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    u = db.query(UnitRelease).filter(UnitRelease.id == unit_id, UnitRelease.project_id == project_id).first()
    if u: db.delete(u); db.commit()
    return {"ok": True}


# ── Client Payment Schedules ────────────────────────────────────────────────────

@router.get("/project/{project_id}/client-payments")
def list_client_payments(project_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    _proj(project_id, user, db)
    items = db.query(ClientPaymentSchedule).filter(ClientPaymentSchedule.project_id == project_id).order_by(ClientPaymentSchedule.display_order, ClientPaymentSchedule.due_date).all()
    today = date.today().isoformat()
    return [{"id": i.id, "milestone_name": i.milestone_name, "description": i.description,
             "amount": i.amount, "percentage_of_contract": i.percentage_of_contract,
             "due_date": i.due_date, "invoice_date": i.invoice_date, "paid_date": i.paid_date,
             "status": "overdue" if (i.status=="invoiced" and i.due_date and i.due_date < today) else i.status,
             "notes": i.notes, "display_order": i.display_order} for i in items]


@router.post("/project/{project_id}/client-payments")
def create_client_payment(project_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    i = ClientPaymentSchedule(
        org_id=p.org_id, project_id=project_id,
        milestone_name=body["milestone_name"], description=body.get("description"),
        amount=body.get("amount"), percentage_of_contract=body.get("percentage_of_contract"),
        due_date=body.get("due_date"), invoice_date=body.get("invoice_date"),
        paid_date=body.get("paid_date"), status=body.get("status","pending"),
        notes=body.get("notes"), display_order=body.get("display_order",100), created_by=user.id,
    )
    db.add(i); db.commit(); db.refresh(i)
    return {"id": i.id, "ok": True}


@router.put("/project/{project_id}/client-payments/{pay_id}")
def update_client_payment(project_id: int, pay_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    i = db.query(ClientPaymentSchedule).filter(ClientPaymentSchedule.id == pay_id, ClientPaymentSchedule.project_id == project_id).first()
    if not i: raise HTTPException(404)
    for f in ["milestone_name","description","amount","percentage_of_contract","due_date","invoice_date","paid_date","status","notes","display_order"]:
        if f in body: setattr(i, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/project/{project_id}/client-payments/{pay_id}")
def delete_client_payment(project_id: int, pay_id: int, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    i = db.query(ClientPaymentSchedule).filter(ClientPaymentSchedule.id == pay_id, ClientPaymentSchedule.project_id == project_id).first()
    if i: db.delete(i); db.commit()
    return {"ok": True}


# ── Specialized Checklists (CMHC, Indigenous, Environmental) ────────────────────

CMHC_ITEMS = [
    ("eligibility","Confirm project meets CMHC eligible property criteria"),
    ("eligibility","Energy efficiency standard certification (Step Code/NECB)"),
    ("documentation","CMHC application form completed"),
    ("documentation","Environmental Phase I/II assessment"),
    ("documentation","Geotechnical report"),
    ("documentation","Appraisal report"),
    ("documentation","Project pro forma and financial statements"),
    ("documentation","Construction contract and cost breakdown"),
    ("documentation","Architect/engineer certificates and plans"),
    ("insurance","Project insurance certificates"),
    ("insurance","Contractor bonds"),
    ("approvals","Municipal permits obtained"),
    ("approvals","Zoning compliance confirmed"),
    ("reporting","Monthly draw inspection reports"),
    ("reporting","Cost-to-complete certifications"),
]

INDIGENOUS_ITEMS = [
    ("consultation","Initial notification sent to Indigenous groups"),
    ("consultation","Duty-to-consult assessment completed"),
    ("consultation","Consultation log initiated and maintained"),
    ("consultation","Indigenous groups identified within study area"),
    ("consultation","Consultation meetings scheduled and held"),
    ("consultation","Comments and concerns recorded and responded to"),
    ("mitigation","Cultural heritage impact assessment"),
    ("mitigation","Environmental mitigation measures agreed"),
    ("mitigation","Impact-benefit agreement (IBA) negotiated"),
    ("approvals","Federal/provincial consultation clearance"),
    ("approvals","Conditions of approval documented"),
    ("monitoring","Post-construction monitoring plan"),
]

@router.get("/project/{project_id}/specialized-checklists")
def list_checklists(project_id: int, checklist_type: str = None, db: Session = Depends(_db), user=Depends(get_current_user)):
    _proj(project_id, user, db)
    q = db.query(SpecializedChecklistItem).filter(SpecializedChecklistItem.project_id == project_id)
    if checklist_type: q = q.filter(SpecializedChecklistItem.checklist_type == checklist_type)
    items = q.order_by(SpecializedChecklistItem.checklist_type, SpecializedChecklistItem.id).all()
    return [{"id": i.id, "checklist_type": i.checklist_type, "category": i.category,
             "item_name": i.item_name, "description": i.description,
             "responsible_party": i.responsible_party, "due_date": i.due_date,
             "status": i.status, "notes": i.notes,
             "completed_at": i.completed_at.isoformat() if i.completed_at else None} for i in items]


@router.post("/project/{project_id}/specialized-checklists/seed")
def seed_checklist(project_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    checklist_type = body.get("checklist_type", "cmhc")
    items_template = CMHC_ITEMS if checklist_type == "cmhc" else INDIGENOUS_ITEMS
    existing = db.query(SpecializedChecklistItem).filter(
        SpecializedChecklistItem.project_id == project_id,
        SpecializedChecklistItem.checklist_type == checklist_type
    ).count()
    if existing: return {"ok": True, "message": f"{checklist_type} checklist already exists"}
    for cat, name in items_template:
        db.add(SpecializedChecklistItem(org_id=p.org_id, project_id=project_id,
                                        checklist_type=checklist_type, category=cat, item_name=name, created_by=user.id))
    db.commit()
    return {"ok": True, "seeded": len(items_template)}


@router.put("/project/{project_id}/specialized-checklists/{item_id}")
def update_checklist_item(project_id: int, item_id: int, body: dict, db: Session = Depends(_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    i = db.query(SpecializedChecklistItem).filter(SpecializedChecklistItem.id == item_id, SpecializedChecklistItem.project_id == project_id).first()
    if not i: raise HTTPException(404)
    for f in ["status","responsible_party","due_date","notes"]:
        if f in body: setattr(i, f, body[f])
    if body.get("status") == "complete" and not i.completed_at:
        i.completed_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
