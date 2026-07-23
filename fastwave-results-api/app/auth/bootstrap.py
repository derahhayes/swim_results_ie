"""ADMIN_EMAILS bootstrap: the only way an admin role gets granted with zero admins yet.

PATCH /api/v1/users/{id}/roles (app.claims.router) requires an existing
admin to call it - so the very first admin has to come from somewhere
else. bootstrap_admins() is that somewhere else: idempotent, run on every
app startup, promotes any already-registered user whose email is listed
in ADMIN_EMAILS. A listed email with no matching user yet is a no-op -
it's picked up on a later startup once that person registers (e.g. after
a redeploy that adds their email to ADMIN_EMAILS).
"""

from sqlalchemy import func, select

from app.audit import write_audit_log
from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models import User


async def bootstrap_admins() -> None:
    emails = get_settings().admin_emails_list
    if not emails:
        return

    async with AsyncSessionLocal() as session:
        # func.lower() rather than relying on User.email already being
        # lowercase - admin_emails_list is always lowercased (app.config),
        # and this must still match even for the odd pre-Step-5 user row
        # created some other way (e.g. app.ingestion.service's
        # _get_or_create_user placeholder users) with mixed-case email.
        users = (
            (await session.execute(select(User).where(func.lower(User.email).in_(emails)))).scalars().all()
        )
        for user in users:
            if not user.isAdmin:
                user.isAdmin = True
                await write_audit_log(session, "admin.bootstrap", None, entity=f"users:{user.id}")
        await session.commit()
