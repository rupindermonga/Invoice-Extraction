"""Ensure every user has at least one organization.

Called on login — idempotent. Creates a personal org named after the user
and migrates their existing projects/invoices to it if not already set.
"""
import re
from sqlalchemy.orm import Session
from .models import User, Organization, OrganizationMember, Project, Invoice

_SLUG_BAD = re.compile(r"[^a-z0-9\-]")
_SLUG_MULTI = re.compile(r"\-{2,}")


def _make_slug(base: str, db: Session) -> str:
    slug = _SLUG_BAD.sub("-", base.lower().strip())
    slug = _SLUG_MULTI.sub("-", slug).strip("-")[:48] or "org"
    # Ensure minimum length
    if len(slug) < 3:
        slug = (slug + "---")[:3]
    # Make unique
    original = slug
    counter = 1
    while db.query(Organization).filter(Organization.slug == slug).first():
        slug = f"{original[:45]}-{counter}"
        counter += 1
    return slug


def ensure_user_org(db: Session, user: User):
    """Create a personal org for a user if they don't already have one.
    Also migrates existing projects/invoices to the org."""
    existing_mem = db.query(OrganizationMember).filter(
        OrganizationMember.user_id == user.id,
        OrganizationMember.is_active == True,
    ).first()

    if existing_mem:
        # Already has an org — migrate any un-scoped data
        org = db.query(Organization).filter(Organization.id == existing_mem.org_id).first()
        if org:
            _migrate_user_data(db, user.id, org.id)
        return

    # Create personal org
    slug = _make_slug(user.username, db)
    org = Organization(name=user.username, slug=slug)
    db.add(org)
    db.flush()

    mem = OrganizationMember(org_id=org.id, user_id=user.id, role="owner")
    db.add(mem)
    db.flush()

    _migrate_user_data(db, user.id, org.id)
    db.commit()


def _migrate_user_data(db: Session, user_id: int, org_id: int):
    """Stamp org_id on any existing projects/invoices that don't have one yet."""
    db.query(Project).filter(
        Project.user_id == user_id,
        Project.org_id.is_(None),
    ).update({"org_id": org_id}, synchronize_session=False)

    db.query(Invoice).filter(
        Invoice.user_id == user_id,
        Invoice.org_id.is_(None),
    ).update({"org_id": org_id}, synchronize_session=False)
