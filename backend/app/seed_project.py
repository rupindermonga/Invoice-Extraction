"""Project structure templates seeded on project creation.

Supported project_type values:
  fiber_build          — FTTH / telecom infrastructure
  residential_const    — Residential construction (houses, semis, townhomes)
  real_estate_dev      — Real estate development (land → sale)
  ici_construction     — ICI / commercial / industrial construction
  renovation           — Renovation or retrofit
  custom               — Blank; user builds their own structure
"""
from sqlalchemy.orm import Session
from .models import Project, SubDivision, CostCategory, CostSubCategory


# ─── Template definitions ─────────────────────────────────────────────────────
#
# Each template: list of (category_name, budget, is_per_subdivision, [subcategory_names])
# Sub-division names and display_order handled per template.

_TEMPLATES = {

    "fiber_build": {
        "name_hint": "FTTH Network Build",
        "subdivisions": ["Zone 1", "Zone 2", "Zone 3", "Zone 4", "Zone 5"],
        "categories": [
            ("Payroll",    0,  False, []),
            ("Material",   0,  False, [
                "Vaults and Handholes",
                "Fibre and Cable Infrastructure",
                "Splicing & Terminations",
                "Distribution & Access Devices",
                "Misc.",
            ]),
            ("Electronics", 0, False, [
                "OLT & Chassis Kits",
                "ONTs & Optical Modules",
                "PSS & Transponders",
                "Network Software & Tools",
                "Professional Services & Training",
            ]),
            ("Make Ready", 0, False, []),
            ("Fiber Build", 0, True,  [  # per sub-division
                "Mobilization",
                "Drawings / Design and Engineering",
                "Aerial Installation",
                "Underground Installation",
                "Drops",
                "Construction Support",
            ]),
        ],
    },

    "residential_const": {
        "name_hint": "Residential Construction",
        "subdivisions": ["Phase A", "Phase B", "Phase C", "Phase D"],
        "categories": [
            ("Site Preparation",     0, False, ["Demolition & Clearing", "Grading & Drainage", "Temporary Facilities"]),
            ("Foundation & Concrete",0, False, ["Footings", "Foundation Walls", "Slab on Grade", "Waterproofing"]),
            ("Structural Framing",   0, False, ["Wood Frame", "Steel Beam & Columns", "Sheathing & Wrap"]),
            ("Electrical",           0, False, ["Rough-in Wiring", "Service Panel & Meter", "Fixtures & Trim"]),
            ("Plumbing",             0, False, ["Rough-in", "Fixtures & Trim", "Water Service"]),
            ("HVAC",                 0, False, ["Equipment Supply", "Ductwork & Venting", "Commissioning"]),
            ("Roofing",              0, False, ["Shingles & Underlayment", "Flashing & Eavestroughs"]),
            ("Windows & Doors",      0, False, ["Windows Supply & Install", "Exterior Doors", "Interior Doors"]),
            ("Insulation",           0, False, ["Batt Insulation", "Spray Foam"]),
            ("Drywall & Finishing",  0, False, ["Drywall Supply", "Taping & Mudding", "Painting"]),
            ("Flooring",             0, False, ["Hardwood", "Tile", "Carpet"]),
            ("Millwork & Cabinetry", 0, False, ["Kitchen Cabinets", "Bathroom Vanities", "Trim & Moulding"]),
            ("Payroll",              0, False, []),
        ],
    },

    "real_estate_dev": {
        "name_hint": "Real Estate Development",
        "subdivisions": ["Phase 1", "Phase 2", "Phase 3"],
        "categories": [
            ("Land & Acquisition",   0, False, ["Purchase Price", "Due Diligence", "Legal Fees", "Transfer Tax"]),
            ("Design & Engineering", 0, False, [
                "Architectural Design", "Structural Engineering", "Civil / Site Engineering",
                "Mechanical & Electrical", "Environmental Studies", "Permits & Approvals",
            ]),
            ("Hard Construction",    0, True,  [   # per phase
                "Sitework & Servicing", "Foundation", "Framing & Structure",
                "MEP Rough-in", "Exterior Envelope", "Interior Finishes",
            ]),
            ("Soft Costs",           0, False, [
                "Project Management", "Legal (Construction)", "Accounting & Audit",
                "Insurance", "Builder's Risk", "Marketing & Sales",
            ]),
            ("Financing Costs",      0, False, [
                "Construction Loan Interest", "Lender Fees", "Appraisal Fees",
                "CMHC / Insurer Premiums",
            ]),
            ("Payroll",              0, False, []),
            ("Contingency",          0, False, []),
        ],
    },

    "ici_construction": {
        "name_hint": "Commercial / Industrial Construction",
        "subdivisions": ["Level B1", "Level 1", "Level 2", "Level 3", "Roof"],
        "categories": [
            ("Site & Demolition",    0, False, ["Excavation", "Demolition", "Shoring", "Dewatering"]),
            ("Concrete Structure",   0, False, ["Footings & Grade Beams", "Columns & Walls", "Slabs", "Post-Tensioning"]),
            ("Steel Structure",      0, False, ["Structural Steel", "Metal Decking", "Miscellaneous Steel"]),
            ("Exterior Envelope",    0, False, ["Masonry / Cladding", "Roofing", "Glazing & Curtain Wall", "Waterproofing"]),
            ("Mechanical",           0, False, ["Plumbing", "HVAC & Controls", "Fire Protection", "Process Piping"]),
            ("Electrical",           0, False, ["Power Distribution", "Lighting", "Data & Communications", "Fire Alarm"]),
            ("Interior Construction",0, False, ["Drywall & Partitions", "Flooring", "Ceilings", "Millwork"]),
            ("Site Civil",           0, False, ["Paving & Hardscape", "Landscaping", "Utilities", "Fencing"]),
            ("General Conditions",   0, False, ["Supervision", "Hoisting", "Temporary Facilities", "Bonds & Insurance"]),
            ("Payroll",              0, False, []),
            ("Contingency",          0, False, []),
        ],
    },

    "renovation": {
        "name_hint": "Renovation Project",
        "subdivisions": ["Area 1", "Area 2", "Area 3"],
        "categories": [
            ("Demolition & Abatement",0, False, ["Selective Demo", "Asbestos / Mould Abatement", "Disposal"]),
            ("Structural Repairs",    0, False, ["Foundation Repair", "Beam / Column Replacement", "Underpinning"]),
            ("Envelope & Roofing",    0, False, ["Roofing Replacement", "Window Replacement", "Cladding"]),
            ("Mechanical Upgrade",    0, False, ["HVAC Replacement", "Plumbing Upgrade", "Sprinkler Retrofit"]),
            ("Electrical Upgrade",    0, False, ["Panel Upgrade", "Wiring Replacement", "Lighting"]),
            ("Interior Finishes",     0, False, ["Drywall & Paint", "Flooring", "Millwork & Cabinets"]),
            ("Accessibility",         0, False, ["Ramps & Elevators", "Washroom Upgrades"]),
            ("General Conditions",    0, False, ["Supervision", "Protection", "Temporary Services"]),
            ("Payroll",               0, False, []),
        ],
    },

    "custom": {
        "name_hint": "",
        "subdivisions": [],
        "categories": [],   # blank — user creates their own
    },
}


# ─── Public API ───────────────────────────────────────────────────────────────

def seed_project_template(db: Session, project_id: int, project_type: str):
    """Seed cost categories and sub-divisions for a new project from a template.
    Idempotent: only runs if the project has no categories yet."""
    from .models import Project as _Proj
    proj = db.query(_Proj).filter(_Proj.id == project_id).first()
    if not proj:
        return

    template = _TEMPLATES.get(project_type) or _TEMPLATES["custom"]

    # Sub-divisions
    existing_sds = db.query(SubDivision).filter(SubDivision.project_id == project_id).count()
    if not existing_sds:
        for i, name in enumerate(template["subdivisions"], start=1):
            db.add(SubDivision(project_id=project_id, name=name, display_order=i * 10))

    # Categories
    existing_cats = db.query(CostCategory).filter(CostCategory.project_id == project_id).count()
    if not existing_cats:
        for order, (cat_name, budget, per_sub, subcats) in enumerate(template["categories"], start=1):
            cat = CostCategory(
                project_id=project_id,
                name=cat_name,
                budget=budget,
                is_per_subdivision=per_sub,
                display_order=order * 10,
            )
            db.add(cat)
            db.flush()
            for so, sc_name in enumerate(subcats, start=1):
                db.add(CostSubCategory(category_id=cat.id, name=sc_name, display_order=so * 10))

    db.commit()


def seed_project_finance(db: Session, user_id: int):
    """Called on first login — creates the user's first project if none exists.
    New projects created via the UI use seed_project_template() instead."""
    existing = db.query(Project).filter(Project.user_id == user_id).first()
    if existing:
        return
    # No project yet — create a blank project; user will pick type from UI
    proj = Project(user_id=user_id, name="My First Project", currency="CAD")
    db.add(proj)
    db.flush()
    # Don't auto-seed categories — user will pick project type from the modal
    db.commit()
