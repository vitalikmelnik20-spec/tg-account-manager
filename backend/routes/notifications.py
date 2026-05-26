import json as _json
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from backend.database import get_db
from backend.models import Notification

router = APIRouter(prefix="/api/notifications")


@router.get("")
async def list_notifications(db: AsyncSession = Depends(get_db)):
    q = await db.execute(
        select(Notification).order_by(Notification.created_at.desc()).limit(100)
    )
    rows = q.scalars().all()
    return [
        {
            "id": r.id,
            "report_type": r.report_type,
            "channel_id": r.channel_id,
            "channel_title": r.channel_title,
            "report_data": _json.loads(r.report_data),
            "is_read": r.is_read,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/unread-count")
async def unread_count(db: AsyncSession = Depends(get_db)):
    q = await db.execute(
        select(func.count()).select_from(Notification).where(Notification.is_read == False)
    )
    return {"count": q.scalar() or 0}


@router.post("/{notif_id}/read")
async def mark_read(notif_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(
        update(Notification).where(Notification.id == notif_id).values(is_read=True)
    )
    await db.commit()
    return {"ok": True}


@router.post("/read-all")
async def mark_all_read(db: AsyncSession = Depends(get_db)):
    await db.execute(update(Notification).values(is_read=True))
    await db.commit()
    return {"ok": True}
