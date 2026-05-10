"""Notification Center — active alerts across all modules for current org."""
from datetime import date, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user, get_current_org
from ..models import (
    OrgVendor, Permit, Bond, Equipment, LenderCovenant,
    InterestReserve, InterestReserveDraw, Invoice, Draw, RFI,
    SafetyIncident, BidPackage, WarrantyItem, Project,
)

router = APIRouter(prefix="/api", tags=["notifications"])


@router.get("/notifications")
def get_notifications(org_ctx=Depends(get_current_org), db: Session = Depends(get_db),
                      user=Depends(get_current_user)):
    """Return all active alerts for the current org, sorted by severity."""
    org, _ = org_ctx
    today = date.today().isoformat()
    warn_date = (date.today() + timedelta(days=30)).isoformat()
    alerts = []

    # Vendor compliance expiries
    try:
        vendors = db.query(OrgVendor).filter(OrgVendor.org_id == org.id, OrgVendor.is_active == True).all()
        for v in vendors:
            if v.wsib_expiry and v.wsib_expiry < today:
                alerts.append({"severity": "critical", "category": "compliance", "icon": "fa-shield-check",
                               "title": f"{v.name} — WSIB Expired", "detail": f"Expired {v.wsib_expiry}", "action": "compliance"})
            elif v.wsib_expiry and v.wsib_expiry <= warn_date:
                alerts.append({"severity": "warning", "category": "compliance", "icon": "fa-shield-check",
                               "title": f"{v.name} — WSIB Expiring", "detail": f"Expires {v.wsib_expiry}", "action": "compliance"})
            if v.insurance_expiry and v.insurance_expiry < today:
                alerts.append({"severity": "critical", "category": "compliance", "icon": "fa-file-shield",
                               "title": f"{v.name} — Insurance Expired", "detail": f"Expired {v.insurance_expiry}", "action": "compliance"})
            elif v.insurance_expiry and v.insurance_expiry <= warn_date:
                alerts.append({"severity": "warning", "category": "compliance", "icon": "fa-file-shield",
                               "title": f"{v.name} — Insurance Expiring", "detail": f"Expires {v.insurance_expiry}", "action": "compliance"})
    except Exception:
        pass

    # Permit expiries (org-wide)
    try:
        permits = db.query(Permit).filter(Permit.org_id == org.id, Permit.status.in_(["issued","applied"])).all()
        for p in permits:
            if p.expiry_date and p.expiry_date < today:
                alerts.append({"severity": "critical", "category": "permits", "icon": "fa-stamp",
                               "title": f"Permit Expired: {p.permit_number or p.description[:30]}", "detail": f"Expired {p.expiry_date}", "action": "permits"})
            elif p.expiry_date and p.expiry_date <= warn_date:
                alerts.append({"severity": "warning", "category": "permits", "icon": "fa-stamp",
                               "title": f"Permit Expiring: {p.permit_number or p.description[:30]}", "detail": f"Expires {p.expiry_date}", "action": "permits"})
    except Exception:
        pass

    # Bond expiries
    try:
        bonds = db.query(Bond).filter(Bond.org_id == org.id, Bond.status == "active").all()
        for b in bonds:
            if b.expiry_date and b.expiry_date < today:
                alerts.append({"severity": "critical", "category": "bonds", "icon": "fa-file-contract",
                               "title": f"{b.bond_type.replace('_',' ').title()} Bond Expired", "detail": f"{b.vendor_name or ''} — {b.expiry_date}", "action": "bonds"})
            elif b.expiry_date and b.expiry_date <= warn_date:
                alerts.append({"severity": "warning", "category": "bonds", "icon": "fa-file-contract",
                               "title": f"{b.bond_type.replace('_',' ').title()} Bond Expiring", "detail": f"{b.vendor_name or ''} — {b.expiry_date}", "action": "bonds"})
    except Exception:
        pass

    # Equipment service/insurance
    try:
        equip = db.query(Equipment).filter(Equipment.org_id == org.id, Equipment.status != "retired").all()
        for e in equip:
            if e.next_service_date and e.next_service_date < today:
                alerts.append({"severity": "warning", "category": "equipment", "icon": "fa-wrench",
                               "title": f"{e.name} — Service Overdue", "detail": f"Due {e.next_service_date}", "action": "equipment"})
            if e.insurance_expiry and e.insurance_expiry < today:
                alerts.append({"severity": "critical", "category": "equipment", "icon": "fa-truck",
                               "title": f"{e.name} — Insurance Expired", "detail": f"Expired {e.insurance_expiry}", "action": "equipment"})
    except Exception:
        pass

    # Covenant breaches
    try:
        covenants = db.query(LenderCovenant).filter(LenderCovenant.org_id == org.id, LenderCovenant.status == "breach").all()
        for c in covenants:
            alerts.append({"severity": "critical", "category": "covenants", "icon": "fa-shield-halved",
                           "title": f"Covenant Breach: {c.name}", "detail": f"Current: {c.current_value} | Threshold: {c.threshold_operator} {c.threshold_value}", "action": "covenants"})
    except Exception:
        pass

    # Open safety incidents (critical severity)
    try:
        critical_incidents = db.query(SafetyIncident).filter(
            SafetyIncident.org_id == org.id, SafetyIncident.status == "open",
            SafetyIncident.severity == "critical"
        ).all()
        for i in critical_incidents:
            alerts.append({"severity": "critical", "category": "safety", "icon": "fa-helmet-safety",
                           "title": "Critical Safety Incident Open", "detail": i.description[:60], "action": "safety"})
    except Exception:
        pass

    # Overdue invoices
    try:
        overdue_count = db.query(Invoice).filter(
            Invoice.org_id == org.id, Invoice.payment_status != "paid",
            Invoice.due_date < today, Invoice.due_date != None
        ).count()
        if overdue_count > 0:
            alerts.append({"severity": "warning", "category": "invoices", "icon": "fa-file-invoice-dollar",
                           "title": f"{overdue_count} Overdue Invoices", "detail": "Payment past due date", "action": "invoices"})
    except Exception:
        pass

    # Overdue RFIs
    try:
        overdue_rfis = db.query(RFI).filter(
            RFI.org_id == org.id, RFI.status == "open", RFI.due_date < today
        ).count()
        if overdue_rfis > 0:
            alerts.append({"severity": "warning", "category": "rfis", "icon": "fa-question-circle",
                           "title": f"{overdue_rfis} Overdue RFIs", "detail": "Response past due", "action": "pm-rfis"})
    except Exception:
        pass

    # Bids due soon
    try:
        soon = (date.today() + timedelta(days=3)).isoformat()
        bid_due = db.query(BidPackage).filter(
            BidPackage.org_id == org.id, BidPackage.status.in_(["issued","receiving"]),
            BidPackage.due_date <= soon, BidPackage.due_date >= today
        ).all()
        for b in bid_due:
            alerts.append({"severity": "info", "category": "bids", "icon": "fa-gavel",
                           "title": f"Bid Due Soon: {b.title[:40]}", "detail": f"Due {b.due_date}", "action": "bid-packages"})
    except Exception:
        pass

    # Sort: critical first, then warning, then info
    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda x: order.get(x["severity"], 3))

    return {
        "alerts": alerts,
        "counts": {
            "critical": sum(1 for a in alerts if a["severity"] == "critical"),
            "warning": sum(1 for a in alerts if a["severity"] == "warning"),
            "info": sum(1 for a in alerts if a["severity"] == "info"),
            "total": len(alerts),
        }
    }
