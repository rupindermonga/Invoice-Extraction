"""AI-powered project finance intelligence.

Feature 1: Invoice → Cost Code Mapper (Gemini)
Feature 2: Lien & Holdback Compliance Brain (Canada rule engine)
Feature 3: Cost Overrun Early Warning (spending velocity)
Feature 4: Draw Intelligence Engine (draw readiness checklist)
Feature 5: Cash Flow Reality Simulator (scenario modeling)
Feature 6: Subcontractor Risk Score (rule-based scoring)
Feature 7: Lender Behavior Model (rejection pattern detection)
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")

def _days_between(a: str, b: str) -> Optional[int]:
    try:
        return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days
    except Exception:
        return None


# ─── Feature 1: Invoice → Cost Code AI Mapper ────────────────────────────────

def suggest_allocation(invoice: Any, categories: List[Any], db: Any) -> dict:
    """Use Gemini to suggest the best cost category + sub-category for an invoice.
    Falls back gracefully if no API key is available."""
    from .gemini import _env_keys
    from ..models import GeminiApiKey

    # Build category list for the prompt
    cat_list = []
    for cat in categories:
        entry = {"id": cat.id, "name": cat.name, "subcategories": []}
        for sc in getattr(cat, "sub_categories", []):
            entry["subcategories"].append({"id": sc.id, "name": sc.name})
        cat_list.append(entry)

    if not cat_list:
        return {"error": "No cost categories defined for this project"}

    # Gather available keys (env first, then DB pool)
    api_keys = _env_keys()
    if not api_keys:
        db_keys = db.query(GeminiApiKey).filter(GeminiApiKey.is_active == True).order_by(GeminiApiKey.priority).all()
        api_keys = [k.key_value for k in db_keys]
    if not api_keys:
        return {"error": "No Gemini API key available"}

    # Build invoice context
    inv_context = {
        "vendor": invoice.vendor_name or "Unknown",
        "total": invoice.total_due or 0,
        "description": None,
        "line_items": [],
    }
    if invoice.extracted_data:
        data = invoice.extracted_data if isinstance(invoice.extracted_data, dict) else {}
        inv_context["description"] = data.get("description") or data.get("notes")
        items = data.get("line_items") or data.get("items") or []
        if isinstance(items, list):
            inv_context["line_items"] = [
                i.get("description") or i.get("name") or str(i)
                for i in items[:5] if isinstance(i, dict)
            ]

    prompt = f"""You are a construction project finance controller.
Given this invoice and the available cost categories, suggest the single best matching category and sub-category.

INVOICE:
- Vendor: {inv_context['vendor']}
- Amount: ${inv_context['total']:,.2f}
- Description: {inv_context['description'] or 'Not provided'}
- Line items: {', '.join(inv_context['line_items']) if inv_context['line_items'] else 'Not provided'}

AVAILABLE CATEGORIES:
{json.dumps(cat_list, indent=2)}

Respond with ONLY valid JSON in this exact format:
{{
  "category_id": <integer id>,
  "category_name": "<name>",
  "sub_category_id": <integer id or null>,
  "sub_category_name": "<name or null>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one sentence>"
}}"""

    import google.generativeai as genai
    last_err = None
    for key in api_keys:
        try:
            genai.configure(api_key=key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(prompt)
            text = resp.text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text.strip())
            # Validate category_id belongs to this project
            valid_cat_ids = {c.id for c in categories}
            if result.get("category_id") not in valid_cat_ids:
                result["category_id"] = None
                result["confidence"] = 0.1
                result["reasoning"] = "AI suggestion not valid for this project — please assign manually."
            return result
        except Exception as e:
            last_err = str(e)
            logger.warning("Gemini key failed for suggest_allocation: %s", e)
            continue

    return {"error": f"AI suggestion failed: {last_err}"}


# ─── Feature 2: Lien & Holdback Compliance Brain ─────────────────────────────

# Canadian holdback rules by province (Construction Acts)
_PROVINCE_RULES = {
    "ON": {
        "name": "Ontario",
        "act": "Construction Act (Ontario)",
        "holdback_pct": 10,
        "release_days_after_substantial": 45,
        "lien_period_days": 60,         # from publication of Certificate of Substantial Performance
        "preservation_days": 90,        # to preserve lien by action
        "notes": "10% holdback mandatory. Lien period 60 days from last supply date or publication of CSP.",
    },
    "BC": {
        "name": "British Columbia",
        "act": "Builders Lien Act (BC)",
        "holdback_pct": 10,
        "release_days_after_substantial": 55,
        "lien_period_days": 45,
        "preservation_days": None,
        "notes": "10% holdback. Head contractor has 55 days after completion to release; 45-day lien filing period.",
    },
    "AB": {
        "name": "Alberta",
        "act": "Builders' Lien Act (Alberta)",
        "holdback_pct": 10,
        "release_days_after_substantial": 45,
        "lien_period_days": 45,
        "preservation_days": 180,
        "notes": "10% holdback. 45-day lien period from last supply. Preserve lien by action within 180 days.",
    },
    "QC": {
        "name": "Quebec",
        "act": "Civil Code of Quebec (Legal Hypothec)",
        "holdback_pct": 0,             # No statutory holdback — contractual only
        "release_days_after_substantial": 30,
        "lien_period_days": 30,        # 30 days from end of work to publish legal hypothec
        "preservation_days": 180,
        "notes": "No statutory holdback. Legal hypothec must be published within 30 days of end of work.",
    },
    "MB": {
        "name": "Manitoba",
        "act": "Builders' Liens Act (Manitoba)",
        "holdback_pct": 7.5,
        "release_days_after_substantial": 40,
        "lien_period_days": 40,
        "preservation_days": None,
        "notes": "7.5% holdback. 40-day lien filing period from last supply date.",
    },
    "SK": {
        "name": "Saskatchewan",
        "act": "Builders' Lien Act (Saskatchewan)",
        "holdback_pct": 10,
        "release_days_after_substantial": 40,
        "lien_period_days": 40,
        "preservation_days": None,
        "notes": "10% holdback. 40-day lien period from last supply.",
    },
    "NS": {
        "name": "Nova Scotia",
        "act": "Builders' Lien Act (NS)",
        "holdback_pct": 10,
        "release_days_after_substantial": 45,
        "lien_period_days": 45,
        "preservation_days": None,
        "notes": "10% holdback. 45-day lien period.",
    },
    "NB": {
        "name": "New Brunswick",
        "act": "Mechanics' Lien Act (NB)",
        "holdback_pct": 10,
        "release_days_after_substantial": 45,
        "lien_period_days": 60,
        "preservation_days": None,
        "notes": "10% holdback. 60-day lien filing period.",
    },
}


def compliance_alerts(project: Any, invoices: List[Any], draws: List[Any], lien_waivers: List[Any]) -> dict:
    """Generate Canadian construction compliance alerts for holdback and lien timelines."""
    today = _today()
    province = "ON"  # default to Ontario; could be derived from project.address in future
    if project.address:
        addr = project.address.upper()
        for code in _PROVINCE_RULES:
            if code in addr or _PROVINCE_RULES[code]["name"].upper() in addr:
                province = code
                break

    rules = _PROVINCE_RULES.get(province, _PROVINCE_RULES["ON"])
    alerts = []
    info = []

    # --- Holdback alerts ---
    unreleased = [i for i in invoices if not getattr(i, "holdback_released", False)
                  and (getattr(i, "holdback_pct", 0) or 0) > 0]

    holdback_eligible = []
    for inv in unreleased:
        inv_date = inv.invoice_date or (str(inv.processed_at)[:10] if inv.processed_at else None)
        if not inv_date:
            continue
        days_old = _days_between(inv_date, today)
        if days_old is None:
            continue
        # Check if enough time has passed to potentially release holdback
        threshold = rules["release_days_after_substantial"]
        if days_old >= threshold:
            holdback_eligible.append({
                "invoice_id": inv.id,
                "vendor": inv.vendor_name or "Unknown",
                "date": inv_date,
                "days_old": days_old,
                "holdback_amt": round((inv.subtotal or inv.total_due or 0) * (inv.holdback_pct or 0) / 100, 2),
            })

    if holdback_eligible:
        total_eligible = sum(e["holdback_amt"] for e in holdback_eligible)
        alerts.append({
            "severity": "warning",
            "type": "holdback_release_eligible",
            "title": f"Holdback Release Eligible — ${total_eligible:,.2f}",
            "message": f"{len(holdback_eligible)} invoice(s) are past the {threshold}-day holdback period under the {rules['act']}. Total holdback eligible for release: ${total_eligible:,.2f}.",
            "items": holdback_eligible[:5],
            "action": "Review and release holdback for eligible invoices",
            "province": province,
            "rule": rules["act"],
        })

    # Holdback approaching eligibility (within 14 days)
    approaching = []
    for inv in unreleased:
        inv_date = inv.invoice_date or (str(inv.processed_at)[:10] if inv.processed_at else None)
        if not inv_date:
            continue
        days_old = _days_between(inv_date, today)
        if days_old is None:
            continue
        threshold = rules["release_days_after_substantial"]
        days_remaining = threshold - days_old
        if 0 < days_remaining <= 14:
            approaching.append({
                "invoice_id": inv.id,
                "vendor": inv.vendor_name or "Unknown",
                "date": inv_date,
                "days_remaining": days_remaining,
                "holdback_amt": round((inv.subtotal or inv.total_due or 0) * (inv.holdback_pct or 0) / 100, 2),
            })
    if approaching:
        alerts.append({
            "severity": "info",
            "type": "holdback_approaching",
            "title": f"Holdback Eligibility Approaching — {len(approaching)} invoice(s)",
            "message": f"{len(approaching)} invoice(s) will be eligible for holdback release within 14 days.",
            "items": approaching[:5],
            "action": "Prepare holdback release documentation",
            "province": province,
            "rule": rules["act"],
        })

    # --- Lien period alerts (invoices with no lien waiver) ---
    waiver_invoice_ids = set()
    for w in lien_waivers:
        # If waiver has a draw_id, match invoices in that draw
        pass  # broad coverage — check vendor instead
    waiver_vendors = {(w.vendor_name or "").lower() for w in lien_waivers if w.vendor_name}

    lien_risk = []
    for inv in invoices:
        vendor = (inv.vendor_name or "").lower()
        if vendor in waiver_vendors:
            continue
        inv_date = inv.invoice_date or (str(inv.processed_at)[:10] if inv.processed_at else None)
        if not inv_date:
            continue
        days_old = _days_between(inv_date, today)
        if days_old is None:
            continue
        lien_window = rules["lien_period_days"]
        days_remaining = lien_window - days_old
        if 0 < days_remaining <= 21:  # warn 21 days before lien period closes
            lien_risk.append({
                "invoice_id": inv.id,
                "vendor": inv.vendor_name or "Unknown",
                "date": inv_date,
                "days_remaining": days_remaining,
                "amount": inv.total_due or 0,
            })

    if lien_risk:
        alerts.append({
            "severity": "high",
            "type": "lien_window_closing",
            "title": f"Lien Filing Window Closing — {len(lien_risk)} vendor(s)",
            "message": f"{len(lien_risk)} unpaid vendor(s) can file a lien within the next 21 days. Collect unconditional lien waivers or pay outstanding balances.",
            "items": lien_risk[:5],
            "action": "Collect lien waivers or issue payment",
            "province": province,
            "rule": rules["act"],
        })

    # --- Missing lien waivers on funded draws ---
    for draw in draws:
        if draw.status not in ("approved", "funded"):
            continue
        draw_invs = [i for i in invoices if i.draw_id == draw.id]
        draw_vendors = {(i.vendor_name or "").lower() for i in draw_invs if i.vendor_name}
        covered = {(w.vendor_name or "").lower() for w in lien_waivers
                   if w.draw_id == draw.id and w.vendor_name and w.waiver_type == "unconditional"}
        missing = draw_vendors - covered
        if missing:
            alerts.append({
                "severity": "warning",
                "type": "missing_lien_waiver",
                "title": f"Draw {draw.draw_number} — Missing Unconditional Lien Waivers",
                "message": f"{len(missing)} vendor(s) in funded Draw {draw.draw_number} have no unconditional lien waiver on file.",
                "items": [{"vendor": v} for v in list(missing)[:5]],
                "action": "Collect unconditional lien waivers before draw close-out",
                "province": province,
                "rule": rules["act"],
            })

    # Info block
    info.append({
        "province": province,
        "province_name": rules["name"],
        "act": rules["act"],
        "holdback_pct": rules["holdback_pct"],
        "release_days": rules["release_days_after_substantial"],
        "lien_period_days": rules["lien_period_days"],
        "notes": rules["notes"],
    })

    total_holdback_held = sum(
        round((i.subtotal or i.total_due or 0) * (getattr(i, "holdback_pct", 0) or 0) / 100, 2)
        for i in unreleased
    )

    return {
        "alerts": alerts,
        "alert_count": len(alerts),
        "province": province,
        "rules": info[0] if info else {},
        "holdback_summary": {
            "total_held": round(total_holdback_held, 2),
            "unreleased_count": len(unreleased),
            "eligible_count": len(holdback_eligible),
        },
    }


# ─── Feature 3: Cost Overrun Early Warning ───────────────────────────────────

def overrun_alerts(project: Any, categories: List[Any], allocations_by_cat: Dict[int, float],
                   change_orders_by_cat: Dict[int, float]) -> dict:
    """Detect categories trending toward budget overrun based on spend velocity."""
    today = _today()
    alerts = []

    start = project.start_date
    end = project.end_date

    # Project timeline progress
    timeline_pct = None
    if start and end:
        total_days = _days_between(start, end)
        elapsed = _days_between(start, today)
        if total_days and total_days > 0 and elapsed is not None:
            timeline_pct = max(0.0, min(100.0, round(elapsed / total_days * 100, 1)))

    category_alerts = []
    for cat in categories:
        co_adj = change_orders_by_cat.get(cat.id, 0.0)
        revised_budget = cat.budget + co_adj
        invoiced = allocations_by_cat.get(cat.id, 0.0)

        if revised_budget <= 0:
            continue

        pct_spent = invoiced / revised_budget * 100
        remaining = revised_budget - invoiced

        severity = None
        message = None

        # Velocity-based: if we've spent more than timeline % with timeline data
        if timeline_pct is not None and timeline_pct > 10:
            if pct_spent > (timeline_pct + 20):
                severity = "high"
                message = f"Spending is {pct_spent:.0f}% of budget but project is only {timeline_pct:.0f}% through its timeline."
            elif pct_spent > (timeline_pct + 10):
                severity = "warning"
                message = f"Spending is {pct_spent:.0f}% of budget vs {timeline_pct:.0f}% project completion — running ahead of schedule."

        # Hard threshold: >90% spent
        if pct_spent >= 95:
            severity = "high"
            message = f"Budget nearly exhausted — {pct_spent:.0f}% spent, only ${remaining:,.2f} remaining."
        elif pct_spent >= 80 and severity != "high":
            severity = "warning"
            message = f"{pct_spent:.0f}% of budget consumed. ${remaining:,.2f} remaining."

        # Over budget
        if invoiced > revised_budget:
            overrun = invoiced - revised_budget
            severity = "critical"
            message = f"OVER BUDGET by ${overrun:,.2f} ({pct_spent:.0f}% of budget spent)."

        if severity:
            projected_final = None
            if timeline_pct and timeline_pct > 5:
                # Linear projection: if we've spent X at Y% timeline, project final = X / (Y/100)
                projected_final = round(invoiced / (timeline_pct / 100), 2) if timeline_pct > 0 else None

            category_alerts.append({
                "category_id": cat.id,
                "category_name": cat.name,
                "severity": severity,
                "budget": round(revised_budget, 2),
                "invoiced": round(invoiced, 2),
                "remaining": round(remaining, 2),
                "pct_spent": round(pct_spent, 1),
                "projected_final": projected_final,
                "projected_overrun": round(projected_final - revised_budget, 2) if projected_final else None,
                "message": message,
            })

    # Sort by severity
    sev_order = {"critical": 0, "high": 1, "warning": 2}
    category_alerts.sort(key=lambda a: sev_order.get(a["severity"], 9))

    # Overall project burn rate
    total_budget = sum(c.budget + change_orders_by_cat.get(c.id, 0.0) for c in categories)
    total_invoiced = sum(allocations_by_cat.values())
    overall_pct = round(total_invoiced / total_budget * 100, 1) if total_budget else 0

    return {
        "alerts": category_alerts,
        "alert_count": len(category_alerts),
        "critical_count": sum(1 for a in category_alerts if a["severity"] == "critical"),
        "high_count": sum(1 for a in category_alerts if a["severity"] == "high"),
        "warning_count": sum(1 for a in category_alerts if a["severity"] == "warning"),
        "overall_pct_spent": overall_pct,
        "timeline_pct": timeline_pct,
        "total_budget": round(total_budget, 2),
        "total_invoiced": round(total_invoiced, 2),
    }


# ─── Feature 4: Draw Intelligence Engine ─────────────────────────────────────

def draw_readiness(draw: Any, invoices: List[Any], lien_waivers: List[Any],
                   subcontractors: List[Any], documents: List[Any]) -> dict:
    """Generate a draw submission readiness checklist — what's ready vs. what's blocking."""
    checklist = []
    blocking = []
    ready = []
    warnings = []

    # 1. Invoice approval status
    pending_approval = [i for i in invoices if i.approval_status == "pending"]
    if pending_approval:
        item = {
            "check": "invoice_approvals",
            "label": "Invoice Approvals",
            "status": "blocking",
            "detail": f"{len(pending_approval)} invoice(s) still pending internal approval.",
            "items": [{"id": i.id, "vendor": i.vendor_name, "amount": i.total_due} for i in pending_approval[:5]],
        }
        blocking.append(item)
    else:
        ready.append({"check": "invoice_approvals", "label": "Invoice Approvals", "status": "ready", "detail": "All invoices approved."})

    # 2. Lender submitted amounts set
    no_submitted = [i for i in invoices if i.lender_submitted_amt is None]
    if no_submitted:
        blocking.append({
            "check": "lender_amounts",
            "label": "Lender Submitted Amounts",
            "status": "blocking",
            "detail": f"{len(no_submitted)} invoice(s) have no lender submitted amount set.",
            "items": [{"id": i.id, "vendor": i.vendor_name, "amount": i.total_due} for i in no_submitted[:5]],
        })
    else:
        ready.append({"check": "lender_amounts", "label": "Lender Submitted Amounts", "status": "ready", "detail": "All submitted amounts set."})

    # 3. Conditional lien waivers
    vendor_names = {(i.vendor_name or "").lower() for i in invoices if i.vendor_name}
    conditional_vendors = {
        (w.vendor_name or "").lower()
        for w in lien_waivers
        if w.draw_id == draw.id and w.waiver_type == "conditional" and w.vendor_name
    }
    missing_conditional = vendor_names - conditional_vendors
    if missing_conditional:
        warnings.append({
            "check": "conditional_waivers",
            "label": "Conditional Lien Waivers",
            "status": "warning",
            "detail": f"{len(missing_conditional)} vendor(s) missing conditional lien waivers.",
            "items": [{"vendor": v} for v in list(missing_conditional)[:5]],
        })
    else:
        ready.append({"check": "conditional_waivers", "label": "Conditional Lien Waivers", "status": "ready", "detail": "Conditional waivers collected from all vendors."})

    # 4. Insurance & WSIB on subcontractors
    today = _today()
    expired_subs = []
    for s in subcontractors:
        issues = []
        if s.insurance_expiry and s.insurance_expiry < today:
            issues.append(f"insurance expired {s.insurance_expiry}")
        if s.wsib_expiry and s.wsib_expiry < today:
            issues.append(f"WSIB expired {s.wsib_expiry}")
        if issues:
            expired_subs.append({"name": s.name, "issues": issues})
    if expired_subs:
        warnings.append({
            "check": "sub_compliance",
            "label": "Subcontractor Compliance",
            "status": "warning",
            "detail": f"{len(expired_subs)} subcontractor(s) have expired insurance or WSIB.",
            "items": expired_subs[:5],
        })
    else:
        ready.append({"check": "sub_compliance", "label": "Subcontractor Compliance", "status": "ready", "detail": "All subcontractors have valid insurance and WSIB."})

    # 5. Supporting documents
    draw_docs = [d for d in documents if d.draw_id == draw.id]
    if not draw_docs:
        warnings.append({
            "check": "draw_documents",
            "label": "Supporting Documents",
            "status": "warning",
            "detail": "No documents attached to this draw. Lenders typically require cost schedules and progress reports.",
        })
    else:
        ready.append({"check": "draw_documents", "label": "Supporting Documents", "status": "ready",
                      "detail": f"{len(draw_docs)} document(s) attached to this draw."})

    # 6. Draw has a submission date
    if not draw.submission_date:
        warnings.append({
            "check": "submission_date",
            "label": "Submission Date",
            "status": "warning",
            "detail": "No submission date set on this draw.",
        })
    else:
        ready.append({"check": "submission_date", "label": "Submission Date", "status": "ready",
                      "detail": f"Submission date: {draw.submission_date}."})

    # 7. Total submitted vs total invoiced sanity check
    total_invoiced = sum(i.total_due or 0 for i in invoices)
    total_submitted = sum(i.lender_submitted_amt or 0 for i in invoices if i.lender_submitted_amt)
    if total_submitted > 0 and total_submitted > total_invoiced * 1.5:
        warnings.append({
            "check": "amount_sanity",
            "label": "Amount Sanity Check",
            "status": "warning",
            "detail": f"Submitted amount (${total_submitted:,.2f}) is more than 150% of invoiced amount (${total_invoiced:,.2f}). Verify margins.",
        })
    elif total_submitted > 0:
        ready.append({"check": "amount_sanity", "label": "Amount Sanity Check", "status": "ready",
                      "detail": f"Submitted ${total_submitted:,.2f} against ${total_invoiced:,.2f} invoiced."})

    checklist = blocking + warnings + ready

    score = 100
    score -= len(blocking) * 25
    score -= len(warnings) * 10
    score = max(0, min(100, score))

    return {
        "draw_id": draw.id,
        "draw_number": draw.draw_number,
        "draw_status": draw.status,
        "readiness_score": score,
        "is_ready": len(blocking) == 0,
        "blocking_count": len(blocking),
        "warning_count": len(warnings),
        "ready_count": len(ready),
        "checklist": checklist,
        "summary": {
            "invoice_count": len(invoices),
            "total_invoiced": round(total_invoiced, 2),
            "total_submitted": round(total_submitted, 2),
        },
    }


# ─── Feature 5: Cash Flow Reality Simulator ──────────────────────────────────

def cashflow_scenarios(base_months: List[dict], project: Any,
                       delay_months: int = 0,
                       cost_inflation_pct: float = 0,
                       draw_delay_days: int = 0) -> dict:
    """Simulate cash flow under stress scenarios vs base case."""
    if not base_months:
        return {"base": [], "stressed": [], "summary": {}}

    stressed = []
    for i, m in enumerate(base_months):
        # Shift month forward by delay
        month_str = m["month"]
        try:
            dt = datetime.strptime(month_str + "-01", "%Y-%m-%d")
            stressed_dt = dt + timedelta(days=delay_months * 30)
            new_month = stressed_dt.strftime("%Y-%m")
        except Exception:
            new_month = month_str

        # Inflate spend
        inflated_spend = round(m["invoiced"] * (1 + cost_inflation_pct / 100), 2)
        inflated_projected = round(m["projected_spend"] * (1 + cost_inflation_pct / 100), 2)

        # Delay draw receipts by draw_delay_days
        draw_receipts = m["draw_receipts"]
        delayed_receipts = 0.0
        if draw_receipts > 0 and draw_delay_days > 0:
            # Push receipt to a later month
            delay_months_shift = draw_delay_days // 30
            try:
                dt = datetime.strptime(month_str + "-01", "%Y-%m-%d")
                delayed_dt = dt + timedelta(days=draw_delay_days)
                delayed_month = delayed_dt.strftime("%Y-%m")
                # For simplicity, set receipts to 0 this month; they'll appear in another month
                # We approximate by reducing this month's receipt by the delay factor
                delayed_receipts = 0.0
            except Exception:
                delayed_receipts = draw_receipts
        else:
            delayed_receipts = draw_receipts

        stressed_net = round(delayed_receipts - inflated_spend, 2)
        stressed.append({
            "month": new_month,
            "invoiced": inflated_spend,
            "paid": m["paid"],
            "draw_receipts": delayed_receipts,
            "projected_spend": inflated_projected,
            "net": stressed_net,
            "original_month": month_str,
        })

    # Compute cumulative for stressed
    cum = 0.0
    for m in stressed:
        cum = round(cum + m["net"], 2)
        m["cumulative"] = cum

    # Summary comparison
    base_total_spend = sum(m["invoiced"] for m in base_months)
    stressed_total_spend = sum(m["invoiced"] for m in stressed)
    base_total_receipts = sum(m["draw_receipts"] for m in base_months)
    stressed_total_receipts = sum(m["draw_receipts"] for m in stressed)
    base_final_position = base_months[-1].get("cumulative", 0) if base_months else 0
    stressed_final_position = stressed[-1].get("cumulative", 0) if stressed else 0

    # Find cash-negative months in stressed scenario
    cash_negative_months = [m["month"] for m in stressed if m.get("cumulative", 0) < 0]

    # Worst cash position
    worst_position = min((m.get("cumulative", 0) for m in stressed), default=0)

    return {
        "base": base_months,
        "stressed": stressed,
        "scenarios": {
            "delay_months": delay_months,
            "cost_inflation_pct": cost_inflation_pct,
            "draw_delay_days": draw_delay_days,
        },
        "summary": {
            "base_total_spend": round(base_total_spend, 2),
            "stressed_total_spend": round(stressed_total_spend, 2),
            "spend_increase": round(stressed_total_spend - base_total_spend, 2),
            "base_final_position": round(base_final_position, 2),
            "stressed_final_position": round(stressed_final_position, 2),
            "position_change": round(stressed_final_position - base_final_position, 2),
            "cash_negative_months": cash_negative_months,
            "worst_cash_position": round(worst_position, 2),
            "risk_level": (
                "critical" if worst_position < -500_000
                else "high" if worst_position < -100_000
                else "medium" if worst_position < 0
                else "low"
            ),
        },
    }


# ─── Feature 6: Subcontractor Risk Score ─────────────────────────────────────

def subcontractor_risk_scores(subcontractors: List[Any], invoices: List[Any],
                               change_orders: List[Any], lien_waivers: List[Any]) -> List[dict]:
    """Score each subcontractor 0–100 (lower = more risky) based on compliance and payment history."""
    today = _today()
    results = []

    for sub in subcontractors:
        score = 100
        risk_factors = []
        positive_factors = []
        sub_name_lower = (sub.name or "").lower()

        # 1. Insurance expiry
        if not sub.insurance_expiry:
            score -= 20
            risk_factors.append({"factor": "No insurance certificate on file", "impact": -20})
        elif sub.insurance_expiry < today:
            score -= 25
            risk_factors.append({"factor": f"Insurance expired {sub.insurance_expiry}", "impact": -25})
        elif _days_between(today, sub.insurance_expiry) is not None and _days_between(today, sub.insurance_expiry) <= 30:
            score -= 10
            risk_factors.append({"factor": f"Insurance expiring soon ({sub.insurance_expiry})", "impact": -10})
        else:
            positive_factors.append({"factor": "Insurance current", "impact": 0})

        # 2. WSIB expiry
        if not sub.wsib_expiry:
            score -= 15
            risk_factors.append({"factor": "No WSIB certificate on file", "impact": -15})
        elif sub.wsib_expiry < today:
            score -= 20
            risk_factors.append({"factor": f"WSIB expired {sub.wsib_expiry}", "impact": -20})
        else:
            positive_factors.append({"factor": "WSIB current", "impact": 0})

        # 3. Change orders — sub as issuer
        sub_cos = [co for co in change_orders
                   if (co.issued_by or "").lower() == sub_name_lower and co.amount > 0]
        if len(sub_cos) >= 3:
            score -= 15
            risk_factors.append({"factor": f"High change order frequency ({len(sub_cos)} COs)", "impact": -15})
        elif len(sub_cos) >= 2:
            score -= 8
            risk_factors.append({"factor": f"{len(sub_cos)} change orders issued", "impact": -8})
        elif len(sub_cos) == 0:
            positive_factors.append({"factor": "No change orders issued", "impact": 0})

        # 4. Lien waivers — unconditional waiver collected?
        has_unconditional = any(
            (w.vendor_name or "").lower() == sub_name_lower and w.waiver_type == "unconditional"
            for w in lien_waivers
        )
        has_conditional = any(
            (w.vendor_name or "").lower() == sub_name_lower and w.waiver_type == "conditional"
            for w in lien_waivers
        )
        if not has_unconditional and not has_conditional:
            score -= 10
            risk_factors.append({"factor": "No lien waivers on file", "impact": -10})
        elif not has_unconditional:
            score -= 5
            risk_factors.append({"factor": "Conditional waiver only (no unconditional)", "impact": -5})
        else:
            positive_factors.append({"factor": "Unconditional lien waiver collected", "impact": 0})

        # 5. Payment — invoices with outstanding balances
        sub_invoices = [i for i in invoices if (i.vendor_name or "").lower() == sub_name_lower]
        overdue_invs = []
        for inv in sub_invoices:
            due = inv.due_date or inv.invoice_date
            if due and inv.payment_status != "paid":
                days = _days_between(due, today) or 0
                if days > 60:
                    overdue_invs.append(inv)
        if overdue_invs:
            score -= 15
            risk_factors.append({"factor": f"{len(overdue_invs)} invoice(s) overdue >60 days", "impact": -15})

        # 6. Status
        if sub.status == "terminated":
            score -= 30
            risk_factors.append({"factor": "Subcontractor terminated", "impact": -30})
        elif sub.status == "complete":
            positive_factors.append({"factor": "Contract completed", "impact": 0})

        # 7. Contract value vs invoiced
        if sub.contract_value:
            sub_invoiced = sum(i.total_due or 0 for i in sub_invoices)
            if sub_invoiced > sub.contract_value * 1.15:
                overrun_pct = round((sub_invoiced / sub.contract_value - 1) * 100, 1)
                score -= 10
                risk_factors.append({"factor": f"Invoiced {overrun_pct}% over contract value", "impact": -10})

        score = max(0, min(100, score))
        risk_level = (
            "critical" if score < 40
            else "high" if score < 60
            else "medium" if score < 75
            else "low"
        )

        results.append({
            "subcontractor_id": sub.id,
            "name": sub.name,
            "trade": sub.trade,
            "status": sub.status,
            "risk_score": score,
            "risk_level": risk_level,
            "risk_factors": risk_factors,
            "positive_factors": positive_factors,
            "contract_value": sub.contract_value,
            "insurance_expiry": sub.insurance_expiry,
            "wsib_expiry": sub.wsib_expiry,
            "summary": (
                f"High risk — {risk_factors[0]['factor']}" if risk_level in ("critical", "high") and risk_factors
                else f"Medium risk — monitor {len(risk_factors)} factor(s)" if risk_level == "medium"
                else "Low risk — all compliance checks passing"
            ),
        })

    results.sort(key=lambda r: r["risk_score"])
    return results


# ─── Feature 7: Lender Behavior Model ────────────────────────────────────────

def lender_insights(draws: List[Any], invoices: List[Any], lien_waivers: List[Any],
                    documents: List[Any]) -> dict:
    """Detect common lender rejection patterns and submission optimization tips."""
    insights = []
    tips = []
    patterns = []

    # Pattern 1: Invoices rejected by lender
    rejected_invs = [i for i in invoices if i.lender_status == "rejected"]
    if rejected_invs:
        total_rejected = sum(i.lender_submitted_amt or i.total_due or 0 for i in rejected_invs)
        patterns.append({
            "pattern": "lender_rejections",
            "title": f"Lender Rejected {len(rejected_invs)} Invoice(s)",
            "detail": f"${total_rejected:,.2f} has been rejected. Review each invoice for missing documentation or margin discrepancies.",
            "severity": "high",
            "items": [{"id": i.id, "vendor": i.vendor_name, "submitted": i.lender_submitted_amt} for i in rejected_invs[:5]],
        })

    # Pattern 2: Partial approvals (approved < submitted)
    partial_invs = [i for i in invoices
                    if i.lender_approved_amt is not None
                    and i.lender_submitted_amt is not None
                    and i.lender_approved_amt < i.lender_submitted_amt * 0.95]
    if partial_invs:
        total_shortfall = sum((i.lender_submitted_amt or 0) - (i.lender_approved_amt or 0) for i in partial_invs)
        patterns.append({
            "pattern": "partial_approvals",
            "title": f"{len(partial_invs)} Invoice(s) Partially Approved",
            "detail": f"Lender approved less than 95% of submitted amount on {len(partial_invs)} invoice(s). Total shortfall: ${total_shortfall:,.2f}. May indicate ineligible cost types or missing backup.",
            "severity": "warning",
            "shortfall": round(total_shortfall, 2),
        })

    # Pattern 3: Draws submitted without complete lien waivers
    for draw in draws:
        if draw.status in ("submitted", "approved", "funded"):
            draw_invs = [i for i in invoices if i.draw_id == draw.id]
            vendors = {(i.vendor_name or "").lower() for i in draw_invs if i.vendor_name}
            covered = {(w.vendor_name or "").lower() for w in lien_waivers
                       if w.draw_id == draw.id and w.vendor_name}
            missing = vendors - covered
            if missing:
                patterns.append({
                    "pattern": "draw_missing_waivers",
                    "title": f"Draw {draw.draw_number}: Submitted Without Complete Lien Waivers",
                    "detail": f"{len(missing)} vendor(s) had no lien waiver when draw was submitted. Lenders commonly reject or hold draws for this reason.",
                    "severity": "warning",
                    "draw_number": draw.draw_number,
                    "missing_vendors": list(missing)[:5],
                })

    # Pattern 4: Average approval rate across all draws
    approved_invs = [i for i in invoices if i.lender_status == "approved" and i.lender_approved_amt]
    submitted_invs = [i for i in invoices if i.lender_submitted_amt]
    if submitted_invs:
        total_sub = sum(i.lender_submitted_amt or 0 for i in submitted_invs)
        total_app = sum(i.lender_approved_amt or 0 for i in approved_invs)
        approval_rate = round(total_app / total_sub * 100, 1) if total_sub > 0 else 0
        if approval_rate < 85:
            tips.append({
                "tip": "low_approval_rate",
                "title": f"Approval Rate: {approval_rate}%",
                "detail": "Overall lender approval rate is below 85%. Review rejected and partially approved invoices to identify recurring issues.",
                "severity": "warning",
            })
        else:
            tips.append({
                "tip": "approval_rate",
                "title": f"Approval Rate: {approval_rate}%",
                "detail": f"${total_app:,.2f} approved of ${total_sub:,.2f} submitted.",
                "severity": "info",
            })

    # Pattern 5: Invoices not yet submitted to lender
    unsubmitted = [i for i in invoices if i.lender_submitted_amt is None and i.approval_status == "approved"]
    if unsubmitted:
        total_unsubmitted = sum(i.total_due or 0 for i in unsubmitted)
        tips.append({
            "tip": "unsubmitted_invoices",
            "title": f"{len(unsubmitted)} Approved Invoice(s) Not Yet Submitted",
            "detail": f"${total_unsubmitted:,.2f} in approved invoices have no lender submitted amount. Add them to a draw to recover costs.",
            "severity": "info",
        })

    # Tips for draw timing
    if len(draws) > 1:
        submitted_draws = [d for d in draws if d.submission_date]
        if len(submitted_draws) >= 2:
            # Check interval between draws
            dates = sorted(d.submission_date for d in submitted_draws)
            intervals = [_days_between(dates[i], dates[i+1]) for i in range(len(dates)-1)]
            intervals = [x for x in intervals if x is not None]
            if intervals:
                avg_interval = sum(intervals) / len(intervals)
                if avg_interval > 60:
                    tips.append({
                        "tip": "draw_frequency",
                        "title": f"Draw Submissions Averaging {avg_interval:.0f} Days Apart",
                        "detail": "Submitting draws more frequently (every 30 days) improves cash flow and reduces lender exposure risk.",
                        "severity": "info",
                    })

    return {
        "patterns": patterns,
        "tips": tips,
        "pattern_count": len(patterns),
        "tip_count": len(tips),
        "total_submitted": round(sum(i.lender_submitted_amt or 0 for i in invoices if i.lender_submitted_amt), 2),
        "total_approved": round(sum(i.lender_approved_amt or 0 for i in invoices if i.lender_approved_amt), 2),
        "total_rejected": round(sum(i.lender_submitted_amt or 0 for i in rejected_invs), 2),
    }
