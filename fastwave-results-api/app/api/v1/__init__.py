from fastapi import APIRouter

from app.api.v1 import events, meets, swimmers

router = APIRouter(prefix="/api/v1")
router.include_router(meets.router)
router.include_router(events.router)
router.include_router(swimmers.router)
