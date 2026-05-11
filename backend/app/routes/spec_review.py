"""AI Specification Review — Gemini analyzes spec documents for conflicts, gaps, inconsistencies."""
import os, json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..dependencies import get_current_user, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES
from ..models import SpecReview, DrawingRegister, Project, GeminiApiKey

router = APIRouter(prefix="/api/project", tags=["spec-review"])


def get_db():
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


# ── AI Spec Reviews ─────────────────────────────────────────────────────────────

@router.get("/{project_id}/spec-reviews")
def list_reviews(project_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _proj(project_id, user, db)
    reviews = db.query(SpecReview).filter(SpecReview.project_id == project_id).order_by(SpecReview.created_at.desc()).all()
    return [{"id": r.id, "filename": r.filename, "status": r.status, "summary": r.summary,
             "total_issues": r.total_issues, "findings": r.findings,
             "created_at": r.created_at.isoformat()} for r in reviews]


@router.post("/{project_id}/spec-reviews/analyze")
async def analyze_spec(project_id: int, file: UploadFile = File(...),
                       db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Upload a specification document (PDF or text) and run Gemini analysis."""
    import google.generativeai as genai
    from google.generativeai import types as genai_types

    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)

    keys = db.query(GeminiApiKey).filter(GeminiApiKey.is_active == True).order_by(GeminiApiKey.priority).all()
    api_key = os.getenv("GEMINI_API_KEY", "")
    for k in keys:
        if k.key_value: api_key = k.key_value; break
    if not api_key: raise HTTPException(503, "No Gemini API key configured")

    contents = await file.read()
    review = SpecReview(
        org_id=p.org_id, project_id=project_id,
        filename=file.filename, status="processing", created_by=user.id,
    )
    db.add(review); db.commit(); db.refresh(review)

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    mime = file.content_type or ("application/pdf" if file.filename.endswith(".pdf") else "text/plain")
    part = genai_types.Part.from_bytes(data=contents, mime_type=mime)

    prompt = """You are a Canadian construction specification expert. Analyze this specification document and identify issues.

Return ONLY a JSON object with this structure:
{
  "summary": "2-3 sentence executive summary of the spec and key findings",
  "findings": [
    {
      "type": "conflict|gap|inconsistency|missing|ambiguity|compliance",
      "severity": "critical|major|minor|info",
      "section": "section reference or 'General'",
      "description": "clear description of the issue",
      "recommendation": "what to do about it"
    }
  ]
}

Focus on:
- Specification conflicts (e.g., two sections calling for different products/methods)
- Missing requirements (referenced items not specified)
- Ambiguous language that could cause disputes
- Canadian code compliance gaps (NBC, OBC, NBCC)
- Scope gaps between divisions
- Performance specification issues
- CCDC contract compatibility

Return only valid JSON, no markdown."""

    try:
        resp = model.generate_content([prompt, part])
        text = resp.text.strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:]).rsplit("```", 1)[0].strip()
        data = json.loads(text)
        findings = data.get("findings", [])
        review.status = "complete"
        review.summary = data.get("summary", "")
        review.findings = findings
        review.total_issues = len(findings)
    except Exception as e:
        review.status = "error"
        review.summary = f"Analysis failed: {str(e)}"
        review.findings = []
        review.total_issues = 0

    db.commit()
    return {"id": review.id, "status": review.status, "total_issues": review.total_issues,
            "summary": review.summary, "findings": review.findings}


@router.delete("/{project_id}/spec-reviews/{rev_id}")
def delete_review(project_id: int, rev_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    r = db.query(SpecReview).filter(SpecReview.id == rev_id, SpecReview.project_id == project_id).first()
    if r: db.delete(r); db.commit()
    return {"ok": True}


# ── Drawing Register ────────────────────────────────────────────────────────────

@router.get("/{project_id}/drawings")
def list_drawings(project_id: int, discipline: str = None, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _proj(project_id, user, db)
    q = db.query(DrawingRegister).filter(DrawingRegister.project_id == project_id)
    if discipline: q = q.filter(DrawingRegister.discipline == discipline)
    drawings = q.order_by(DrawingRegister.discipline, DrawingRegister.drawing_number).all()
    return [{"id": d.id, "drawing_number": d.drawing_number, "title": d.title,
             "discipline": d.discipline, "current_revision": d.current_revision,
             "revision_date": d.revision_date, "status": d.status, "notes": d.notes,
             "created_at": d.created_at.isoformat()} for d in drawings]


@router.post("/{project_id}/drawings")
def create_drawing(project_id: int, body: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    d = DrawingRegister(
        org_id=p.org_id, project_id=project_id,
        drawing_number=body["drawing_number"], title=body["title"],
        discipline=body.get("discipline"), current_revision=body.get("current_revision"),
        revision_date=body.get("revision_date"), status=body.get("status", "issued"),
        notes=body.get("notes"), created_by=user.id,
    )
    db.add(d); db.commit(); db.refresh(d)
    return {"id": d.id, "ok": True}


@router.put("/{project_id}/drawings/{drw_id}")
def update_drawing(project_id: int, drw_id: int, body: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    d = db.query(DrawingRegister).filter(DrawingRegister.id == drw_id, DrawingRegister.project_id == project_id).first()
    if not d: raise HTTPException(404)
    for f in ["drawing_number","title","discipline","current_revision","revision_date","status","notes"]:
        if f in body: setattr(d, f, body[f])
    db.commit()
    return {"ok": True}


@router.delete("/{project_id}/drawings/{drw_id}")
def delete_drawing(project_id: int, drw_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    d = db.query(DrawingRegister).filter(DrawingRegister.id == drw_id, DrawingRegister.project_id == project_id).first()
    if d: db.delete(d); db.commit()
    return {"ok": True}
