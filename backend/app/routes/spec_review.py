"""AI Specification Review + AI Schedule Generation from drawings."""
import os, json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db as _get_db
from ..dependencies import get_current_user, require_org_member, FINANCE_READ_ROLES, FINANCE_WRITE_ROLES, get_gemini_key
from ..models import SpecReview, DrawingRegister, Project, GeminiApiKey

# alias so inner functions can use get_db
get_db = _get_db

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
    api_key = get_gemini_key()
    for k in keys:
        if k.key_value: api_key = k.key_value; break
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


# ── AI Schedule Generation ──────────────────────────────────────────────────────

@router.post("/{project_id}/generate-schedule")
async def generate_schedule_from_drawing(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Use Gemini Vision to analyze a drawing/spec and generate a construction schedule.

    Returns a list of tasks ready to import into the PM Tasks module.
    """
    import google.generativeai as genai
    from google.generativeai import types as genai_types

    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)

    keys = db.query(GeminiApiKey).filter(GeminiApiKey.is_active == True).order_by(GeminiApiKey.priority).all()
    api_key = get_gemini_key()
    for k in keys:
        if k.key_value: api_key = k.key_value; break
    contents = await file.read()
    mime = file.content_type or ("application/pdf" if file.filename.endswith(".pdf") else "image/jpeg")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    part = genai_types.Part.from_bytes(data=contents, mime_type=mime)

    prompt = """You are a Canadian construction project manager. Analyze this drawing or specification document
and generate a realistic construction schedule.

Return ONLY a JSON array of task objects. Each task:
{
  "title": "clear task name",
  "trade": "Framing | Electrical | Plumbing | Mechanical | Concrete | Finishing | etc.",
  "duration_days": 5,
  "priority": "high | medium | low",
  "predecessors": ["exact title of predecessor task"],
  "notes": "optional brief note"
}

Rules:
- Generate 15-35 tasks covering the full scope visible in the document
- Use realistic Canadian construction durations
- Show logical sequencing (foundations before framing, rough-in before drywall, etc.)
- Include: demolition/site prep, structural, envelope, rough-ins, finishing, commissioning, closeout
- Predecessors list the titles of tasks that must finish before this one starts
- Return only the JSON array, no markdown, no explanation"""

    try:
        resp = model.generate_content([prompt, part])
        text = resp.text.strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:]).rsplit("```", 1)[0].strip()
        tasks = json.loads(text)
        # Add start/end dates based on today + project start
        from datetime import date, timedelta
        today = date.today()
        proj = db.query(Project).filter(Project.id == project_id).first()
        start = proj.start_date if proj and proj.start_date else today.isoformat()
        try:
            current_date = date.fromisoformat(start)
        except Exception:
            current_date = today
        # Assign rough start dates (simplified — no real dependency resolution)
        cumulative_days = 0
        for task in tasks:
            task["start_date"] = (current_date + timedelta(days=cumulative_days)).isoformat()
            task["end_date"] = (current_date + timedelta(days=cumulative_days + task.get("duration_days", 5) - 1)).isoformat()
            task["due_date"] = task["end_date"]
            task["status"] = "not_started"
            task["percent_complete"] = 0
            task["task_type"] = "task"
            cumulative_days += task.get("duration_days", 5)

        return {"tasks": tasks, "count": len(tasks), "source_file": file.filename}
    except Exception as e:
        raise HTTPException(500, f"Schedule generation failed: {str(e)}")


@router.post("/{project_id}/generate-schedule/import")
def import_generated_schedule(project_id: int, body: dict,
                              db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Import AI-generated tasks into the PM Tasks module."""
    from ..models import Task
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    tasks = body.get("tasks", [])
    created = []
    for t in tasks:
        task = Task(
            org_id=p.org_id, project_id=project_id,
            title=t.get("title", "Task"),
            description=t.get("notes"),
            task_type="task",
            status="not_started",
            priority=t.get("priority", "medium"),
            start_date=t.get("start_date"),
            end_date=t.get("end_date"),
            due_date=t.get("due_date"),
            percent_complete=0,
            tags=t.get("trade", ""),
            created_by=user.id,
        )
        db.add(task)
        created.append(task)
    db.commit()
    return {"imported": len(created), "ok": True}


# ── AI Spec Q&A ─────────────────────────────────────────────────────────────────

@router.post("/{project_id}/spec-qa")
async def spec_qa(project_id: int, body: dict,
                  db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Ask a question about a specification. Gemini answers using spec context."""
    import google.generativeai as genai
    p = _proj(project_id, user, db)
    keys = db.query(GeminiApiKey).filter(GeminiApiKey.is_active == True).order_by(GeminiApiKey.priority).all()
    api_key = get_gemini_key()
    for k in keys:
        if k.key_value: api_key = k.key_value; break
    spec_context = body.get("spec_context", "")  # Optional spec text pasted by user
    if not question: raise HTTPException(400, "Question is required")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    prompt = f"""You are a Canadian construction specification expert. Answer this question about construction specifications.
Be specific, cite section types/formats, and reference Canadian standards (NBC, NBCC, CSA, CCDC) where applicable.
Project: {p.name} (Province: {p.province or 'ON'})
{f'Spec Context:{chr(10)}{spec_context[:3000]}' if spec_context else ''}
Question: {question}
Provide a clear, practical answer in 2-4 paragraphs. Include any cautions or Canadian-specific notes."""
    try:
        resp = model.generate_content(prompt)
        return {"question": question, "answer": resp.text.strip(), "project": p.name}
    except Exception as e:
        raise HTTPException(500, f"Q&A failed: {str(e)}")


# ── AI RFI Generator ─────────────────────────────────────────────────────────────

@router.post("/{project_id}/generate-rfi")
async def generate_rfi(project_id: int, body: dict,
                       db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Generate a professional RFI from a drawing conflict or spec ambiguity description."""
    import google.generativeai as genai
    p = _proj(project_id, user, db)
    keys = db.query(GeminiApiKey).filter(GeminiApiKey.is_active == True).order_by(GeminiApiKey.priority).all()
    api_key = get_gemini_key()
    for k in keys:
        if k.key_value: api_key = k.key_value; break
    if not issue: raise HTTPException(400, "Issue description is required")
    from ..models import RFI
    last = db.query(RFI).filter(RFI.project_id == project_id).order_by(RFI.id.desc()).first()
    next_num = f"RFI-{((int(last.rfi_number.split('-')[1]) if last and last.rfi_number else 0) + 1):03d}"
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    prompt = f"""You are a construction project manager. Draft a professional RFI (Request for Information) for a Canadian construction project.
Project: {p.name}
Issue Description: {issue}
Format as JSON:
{{"rfi_number": "{next_num}", "subject": "concise subject line", "description": "formal RFI description referencing applicable spec sections, drawing numbers, or contract clauses (2-3 sentences)", "priority": "high|medium|low", "suggested_response_direction": "suggested answer or clarification needed"}}
Be specific and professional. Reference CSI division numbers and CCDC/Canadian standards where applicable. Return only valid JSON."""
    try:
        resp = model.generate_content(prompt)
        text = resp.text.strip()
        if text.startswith("```"): text = "\n".join(text.split("\n")[1:]).rsplit("```",1)[0].strip()
        rfi_data = json.loads(text)
        return {"rfi": rfi_data, "ready_to_import": True}
    except Exception as e:
        raise HTTPException(500, f"RFI generation failed: {str(e)}")


@router.post("/{project_id}/generate-rfi/import")
def import_rfi(project_id: int, body: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Import an AI-generated RFI into the PM RFI module."""
    from ..models import RFI
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    rfi_data = body.get("rfi", {})
    r = RFI(
        org_id=p.org_id, project_id=project_id,
        rfi_number=rfi_data.get("rfi_number", "RFI-001"),
        subject=rfi_data.get("subject", "AI Generated RFI"),
        description=rfi_data.get("description"),
        priority=rfi_data.get("priority", "medium"),
        status="open", created_by=user.id,
    )
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "rfi_number": r.rfi_number, "ok": True}


# ── AI Submittal Log Generator ───────────────────────────────────────────────────

@router.post("/{project_id}/generate-submittal-log")
async def generate_submittal_log(project_id: int, file: UploadFile = File(None),
                                  body_text: str = None,
                                  db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Upload a spec PDF or paste spec text — Gemini generates a complete submittal log."""
    import google.generativeai as genai
    from google.generativeai import types as genai_types
    p = _proj(project_id, user, db)
    keys = db.query(GeminiApiKey).filter(GeminiApiKey.is_active == True).order_by(GeminiApiKey.priority).all()
    api_key = get_gemini_key()
    for k in keys:
        if k.key_value: api_key = k.key_value; break
    model = genai.GenerativeModel("gemini-2.0-flash")
    prompt = """You are a Canadian construction project manager. Generate a complete submittal log from this specification document.
Return ONLY a JSON array of submittal objects:
[{"submittal_number":"SUB-001","title":"concise submittal name","spec_section":"e.g. 03 30 00","description":"what needs to be submitted","submittal_type":"shop_drawing|sample|data|certificate|test_report|warranty","review_duration_days":14,"priority":"high|medium|low"}]
Include ALL submittals required by the spec (shop drawings, product data, samples, test reports, certifications, warranties).
Use Canadian CSI MasterFormat section numbering. Return only the JSON array."""
    try:
        if file:
            contents = await file.read()
            mime = file.content_type or "application/pdf"
            part = genai_types.Part.from_bytes(data=contents, mime_type=mime)
            resp = model.generate_content([prompt, part])
        else:
            spec_text = body_text or ""
            resp = model.generate_content(f"{prompt}\n\nSpec text:\n{spec_text[:8000]}")
        text = resp.text.strip()
        if text.startswith("```"): text = "\n".join(text.split("\n")[1:]).rsplit("```",1)[0].strip()
        submittals = json.loads(text)
        return {"submittals": submittals, "count": len(submittals), "ready_to_import": True}
    except Exception as e:
        raise HTTPException(500, f"Submittal log generation failed: {str(e)}")


@router.post("/{project_id}/generate-submittal-log/import")
def import_submittal_log(project_id: int, body: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Import AI-generated submittals into the PM Submittals module."""
    from ..models import Submittal
    p = _proj(project_id, user, db)
    require_org_member(db, p.org_id, user.id, FINANCE_WRITE_ROLES)
    submittals = body.get("submittals", [])
    created = []
    for s in submittals:
        sub = Submittal(
            org_id=p.org_id, project_id=project_id,
            submittal_number=s.get("submittal_number", "SUB-001"),
            title=s.get("title", "Submittal"),
            description=s.get("description"),
            spec_section=s.get("spec_section"),
            status="draft", created_by=user.id,
        )
        db.add(sub); created.append(sub)
    db.commit()
    return {"imported": len(created), "ok": True}
