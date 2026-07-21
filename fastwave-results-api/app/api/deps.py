"""Shared FastAPI dependencies: published-meet/event gates, pagination.

The published-meet/event gates are the single place the "publishedAt IS
NOT NULL, else 404" rule and the meet-scoped ETag get enforced, so routes
just declare `Depends(get_published_meet)` instead of copy-pasting the
lookup + gate + etag dance.
"""

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.caching import NotModified, make_etag
from app.db import get_db
from app.models import Meet, MeetEvent


def _meet_etag(meet: Meet) -> str:
    return make_etag(meet.id, str(int(meet.publishedAt.timestamp())))


def _apply_etag_gate(request: Request, etag: str) -> None:
    request.state.etag = etag
    if request.headers.get("if-none-match") == etag:
        raise NotModified(etag)


async def get_published_meet(
    meetId: str, request: Request, session: AsyncSession = Depends(get_db)
) -> Meet:
    meet = await session.get(Meet, meetId)
    if meet is None or meet.publishedAt is None:
        # Same 404 either way - an unpublished meet must not be
        # distinguishable from one that doesn't exist.
        raise HTTPException(status_code=404, detail="Meet not found")

    _apply_etag_gate(request, _meet_etag(meet))
    return meet


async def get_published_event(
    eventId: str, request: Request, session: AsyncSession = Depends(get_db)
) -> tuple[MeetEvent, Meet]:
    event = await session.get(MeetEvent, eventId)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    meet = await session.get(Meet, event.meetId)
    if meet is None or meet.publishedAt is None:
        raise HTTPException(status_code=404, detail="Event not found")

    _apply_etag_gate(request, _meet_etag(meet))
    return event, meet


class PageParams:
    def __init__(self, page: int, pageSize: int):
        self.page = page
        self.pageSize = pageSize

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.pageSize


def pagination(
    page: int = Query(1, ge=1, description="1-based page number"),
    pageSize: int = Query(50, ge=1, le=200, description="Items per page"),
) -> PageParams:
    return PageParams(page=page, pageSize=pageSize)
