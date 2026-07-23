"""Shared audit_log writer - every privileged action writes exactly one row.

Not just claim/affiliation/upload/publish/match-review decisions (the
explicit BRD list) - role grants get one too, since "every privileged
action" is the stated principle and a manual role grant is clearly one.
"""

import json
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def write_audit_log(
    session: AsyncSession,
    action: str,
    user_id: Optional[str],
    entity: Optional[str] = None,
    detail: Optional[dict] = None,
) -> None:
    session.add(
        AuditLog(
            userId=user_id,
            action=action,
            entity=entity,
            detail=json.dumps(detail) if detail is not None else None,
        )
    )
