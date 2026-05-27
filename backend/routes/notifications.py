import json as _json
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, delete as sql_delete

from backend.database import get_db
from backend.models import Notification, NotifChannelDisabled

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


@router.get("/channel-filters")
async def get_channel_filters(db: AsyncSession = Depends(get_db)):
    q = await db.execute(
        select(Notification.channel_id, Notification.channel_title)
        .where(Notification.report_type != "inbox")
        .group_by(Notification.channel_id, Notification.channel_title)
    )
    rows = q.all()
    q2 = await db.execute(select(NotifChannelDisabled.channel_id))
    disabled_ids = {r[0] for r in q2.all()}
    return [
        {"channel_id": r[0], "channel_title": r[1], "enabled": r[0] not in disabled_ids}
        for r in rows
    ]


@router.post("/read-all")
async def mark_all_read(db: AsyncSession = Depends(get_db)):
    await db.execute(update(Notification).values(is_read=True))
    await db.commit()
    return {"ok": True}


@router.delete("")
async def delete_all_notifications(db: AsyncSession = Depends(get_db)):
    await db.execute(sql_delete(Notification))
    await db.commit()
    return {"ok": True}


@router.post("/{notif_id}/read")
async def mark_read(notif_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(
        update(Notification).where(Notification.id == notif_id).values(is_read=True)
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{notif_id}")
async def delete_notification(notif_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(sql_delete(Notification).where(Notification.id == notif_id))
    await db.commit()
    return {"ok": True}


@router.post("/channel-filters/{channel_id}/disable")
async def disable_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    existing = await db.get(NotifChannelDisabled, channel_id)
    if not existing:
        db.add(NotifChannelDisabled(channel_id=channel_id))
        await db.commit()
    return {"ok": True}


@router.post("/channel-filters/{channel_id}/enable")
async def enable_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(
        sql_delete(NotifChannelDisabled).where(NotifChannelDisabled.channel_id == channel_id)
    )
    await db.commit()
    return {"ok": True}
