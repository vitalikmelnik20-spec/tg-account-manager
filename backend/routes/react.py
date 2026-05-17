import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from backend.database import get_db, AsyncSessionLocal
from backend.models import ReactChannel
from backend.tg_manager import tg_manager

router = APIRouter(prefix="/api/react")

_state = {
    "running": False,
    "task": None,
    "log": [],
    "total_reacted": 0,
    "total_viewed": 0,
    "total_errors": 0,
    "account_ids": [],
}


def _ts():
    return datetime.now().strftime("%H:%M:%S")


async def _broadcast(entry: dict):
    from backend.routes.ws import manager as ws_manager
    _state["log"].append(entry)
    if len(_state["log"]) > 5000:
        _state["log"] = _state["log"][-4000:]
    await ws_manager.broadcast({"type": "react_log", **entry})


async def _push_stats():
    from backend.routes.ws import manager as ws_manager
    await ws_manager.broadcast({
        "type": "react_stats",
        "running": _state["running"],
        "total_reacted": _state["total_reacted"],
        "total_viewed": _state["total_viewed"],
        "total_errors": _state["total_errors"],
    })


async def _acc_label(client, account_id: int) -> str:
    try:
        me = await client.get_me()
        name = me.first_name or f"#{account_id}"
        return f"{name} (@{me.username})" if me.username else name
    except Exception:
        return f"#{account_id}"


async def _do_react(client, entity, msg_id: int, reaction: str, ch_title: str, account_id: int):
    from telethon.tl.functions.messages import SendReactionRequest, GetMessagesViewsRequest
    from telethon.tl.types import ReactionEmoji

    # View (increment)
    try:
        await client(GetMessagesViewsRequest(peer=entity, id=[msg_id], increment=True))
        _state["total_viewed"] += 1
    except Exception:
        pass

    # React
    try:
        await client(SendReactionRequest(
            peer=entity,
            msg_id=msg_id,
            reaction=[ReactionEmoji(emoticon=reaction)],
        ))
        _state["total_reacted"] += 1
        label = await _acc_label(client, account_id)
        await _broadcast({"level": "ok", "msg": f"[{_ts()}] ✓  [{ch_title}]  {reaction}  [{label}]  пост #{msg_id}"})
    except Exception as e:
        _state["total_errors"] += 1
        label = await _acc_label(client, account_id)
        await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{ch_title}]  [{label}]  {str(e)[:80]}"})


async def _poll_channel(ch, poll_client):
    from telethon.tl.types import InputPeerChannel

    try:
        entity = await poll_client.get_entity(InputPeerChannel(ch.channel_id, ch.access_hash))
    except Exception as e:
        await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{ch.title}]  не знайдено: {str(e)[:60]}"})
        return

    new_msgs = []
    async for msg in poll_client.iter_messages(entity, limit=10):
        if msg.id <= ch.last_msg_id:
            break
        new_msgs.append(msg)

    if not new_msgs:
        return

    ch.last_msg_id = max(m.id for m in new_msgs)
    newest = max(new_msgs, key=lambda m: m.id)

    text_preview = (newest.message or "")[:50].replace("\n", " ")
    await _broadcast({"level": "info", "msg": f"[{_ts()}] 📢  [{ch.title}]  новий пост #{newest.id}  «{text_preview}»"})

    # All selected accounts react
    for aid in _state["account_ids"]:
        if not _state["running"]:
            break
        client = tg_manager.clients.get(aid)
        if not client:
            continue
        await _do_react(client, entity, newest.id, ch.reaction, ch.title, aid)
        await asyncio.sleep(1)


async def _init_last_ids(db):
    result = await db.execute(
        select(ReactChannel).where(
            ReactChannel.last_msg_id == 0,
            ReactChannel.enabled == True,
        )
    )
    poll_aid = next(
        (a for a in _state["account_ids"] if tg_manager.clients.get(a)),
        next(iter(tg_manager.clients), None),
    )
    if not poll_aid:
        return
    client = tg_manager.clients.get(poll_aid)
    for ch in result.scalars().all():
        try:
            from telethon.tl.types import InputPeerChannel
            entity = await client.get_entity(InputPeerChannel(ch.channel_id, ch.access_hash))
            async for msg in client.iter_messages(entity, limit=1):
                ch.last_msg_id = msg.id
                await _broadcast({"level": "info", "msg": f"[{_ts()}] ℹ  [{ch.title}]  стартова позиція: пост #{msg.id}"})
                break
        except Exception as e:
            await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⚠  [{ch.title}]  {str(e)[:60]}"})
    await db.commit()


async def _catchup_channel(ch, poll_client):
    from telethon.tl.types import InputPeerChannel
    try:
        entity = await poll_client.get_entity(InputPeerChannel(ch.channel_id, ch.access_hash))
    except Exception as e:
        await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{ch.title}]  {str(e)[:60]}"})
        return

    msgs = []
    async for msg in poll_client.iter_messages(entity, limit=50):
        msgs.append(msg)
    if not msgs:
        return

    msgs.reverse()  # oldest → newest
    await _broadcast({"level": "info", "msg": f"[{_ts()}] 📜  [{ch.title}]  надоганяємо {len(msgs)} постів..."})

    for msg in msgs:
        if not _state["running"]:
            break
        for aid in _state["account_ids"]:
            if not _state["running"]:
                break
            client = tg_manager.clients.get(aid)
            if not client:
                continue
            await _do_react(client, entity, msg.id, ch.reaction, ch.title, aid)
            await asyncio.sleep(1)

    ch.last_msg_id = max(m.id for m in msgs)


async def _react_loop(catchup: bool = False):
    await _broadcast({"level": "info", "msg": f"[{_ts()}] ▶ АВТО-РЕАКЦІЇ СТАРТ  акаунтів: {len(_state['account_ids'])}  чекаємо 30с..."})
    await asyncio.sleep(30)

    async with AsyncSessionLocal() as db:
        if catchup:
            result = await db.execute(select(ReactChannel).where(ReactChannel.enabled == True))
            channels = result.scalars().all()
            poll_aid = next(
                (a for a in _state["account_ids"] if tg_manager.clients.get(a)),
                next(iter(tg_manager.clients), None),
            )
            if poll_aid:
                client = tg_manager.clients.get(poll_aid)
                await _broadcast({"level": "info", "msg": f"[{_ts()}] 📜 Режим надолуження: останні 50 постів у кожному каналі"})
                for ch in channels:
                    if not _state["running"]:
                        break
                    try:
                        await _catchup_channel(ch, client)
                    except Exception as e:
                        await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{ch.title}]  {str(e)[:80]}"})
            await db.commit()
        else:
            await _init_last_ids(db)

    await _broadcast({"level": "info", "msg": f"[{_ts()}] 🔄 Опитування каналів кожні 60с"})

    while _state["running"]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(ReactChannel).where(ReactChannel.enabled == True))
            channels = result.scalars().all()

            poll_aid = next(
                (a for a in _state["account_ids"] if tg_manager.clients.get(a)),
                next(iter(tg_manager.clients), None),
            )

            for ch in channels:
                if not _state["running"]:
                    break
                if not poll_aid:
                    await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⚠ немає акаунтів"})
                    break
                client = tg_manager.clients.get(poll_aid)
                if not client:
                    continue
                try:
                    await _poll_channel(ch, client)
                except Exception as e:
                    await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{ch.title}]  {str(e)[:100]}"})

            await db.commit()

        await _push_stats()

        for _ in range(60):
            if not _state["running"]:
                break
            await asyncio.sleep(1)

    _state["running"] = False
    await _broadcast({
        "level": "info",
        "msg": f"[{_ts()}] ■ ЗУПИНЕНО  ✓{_state['total_reacted']} реакцій  👁{_state['total_viewed']} переглядів  ✕{_state['total_errors']} помилок",
    })
    await _push_stats()


# ── Routes ──────────────────────────────────────────────

class AddReactChannelReq(BaseModel):
    link: str
    reaction: str = "👍"
    account_id: Optional[int] = None


class AddReactChannelsBulkReq(BaseModel):
    links: list[str]
    reaction: str = "👍"
    account_id: Optional[int] = None


class StartReactReq(BaseModel):
    account_ids: list[int] = []
    catchup: bool = False


@router.post("/start")
async def react_start(req: StartReactReq):
    if _state["running"]:
        return {"error": "Вже запущено"}
    _state["running"] = True
    _state["log"] = []
    _state["total_reacted"] = 0
    _state["total_viewed"] = 0
    _state["total_errors"] = 0
    _state["account_ids"] = req.account_ids or list(tg_manager.clients.keys())
    _state["task"] = asyncio.create_task(_react_loop(catchup=req.catchup))
    return {"ok": True}


@router.post("/stop")
async def react_stop():
    _state["running"] = False
    task = _state.get("task")
    if task and not task.done():
        task.cancel()
    return {"ok": True}


@router.get("/status")
async def react_status():
    return {
        "running": _state["running"],
        "total_reacted": _state["total_reacted"],
        "total_viewed": _state["total_viewed"],
        "total_errors": _state["total_errors"],
        "account_ids": _state["account_ids"],
        "log": _state["log"][-500:],
    }


@router.get("/channels")
async def list_react_channels(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ReactChannel).order_by(ReactChannel.created_at))
    return [
        {
            "id": ch.id,
            "title": ch.title,
            "username": ch.username,
            "channel_id": ch.channel_id,
            "reaction": ch.reaction,
            "last_msg_id": ch.last_msg_id,
            "enabled": ch.enabled,
        }
        for ch in result.scalars().all()
    ]


@router.post("/channels")
async def add_react_channel(req: AddReactChannelReq, db: AsyncSession = Depends(get_db)):
    if not tg_manager.clients:
        raise HTTPException(400, "Немає підключених акаунтів")
    try:
        entity = await _get_entity_any_client(req.link.strip(), req.account_id)
        ch = ReactChannel(
            channel_id=entity.id,
            access_hash=entity.access_hash,
            username=getattr(entity, "username", None),
            title=entity.title,
            reaction=req.reaction or "👍",
            last_msg_id=0,
            enabled=True,
        )
        db.add(ch)
        await db.commit()
        await db.refresh(ch)
        return {"id": ch.id, "title": ch.title}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Не вдалось знайти канал: {e}")


async def _get_entity_any_client(link: str, preferred_aid=None):
    """Try all connected clients until one resolves the entity (skips FloodWait accounts)."""
    from telethon.errors import FloodWaitError
    candidates = list(tg_manager.clients.keys())
    if preferred_aid and preferred_aid in tg_manager.clients:
        candidates = [preferred_aid] + [a for a in candidates if a != preferred_aid]
    last_err = None
    for aid in candidates:
        client = tg_manager.clients.get(aid)
        if not client:
            continue
        try:
            return await client.get_entity(link)
        except FloodWaitError:
            continue
        except Exception as e:
            last_err = e
    raise last_err or Exception("Немає доступних акаунтів")


@router.post("/channels/bulk")
async def add_react_channels_bulk(req: AddReactChannelsBulkReq, db: AsyncSession = Depends(get_db)):
    if not tg_manager.clients:
        raise HTTPException(400, "Немає підключених акаунтів")

    existing = {r for r in (await db.execute(select(ReactChannel.channel_id))).scalars()}

    results = []
    for link in req.links:
        link = link.strip()
        if not link:
            continue
        try:
            entity = await _get_entity_any_client(link, req.account_id)
            if entity.id in existing:
                results.append({"link": link, "ok": False, "error": "вже додано"})
                continue
            db.add(ReactChannel(
                channel_id=entity.id,
                access_hash=entity.access_hash,
                username=getattr(entity, "username", None),
                title=entity.title,
                reaction=req.reaction or "👍",
                last_msg_id=0,
                enabled=True,
            ))
            existing.add(entity.id)
            results.append({"link": link, "ok": True, "title": entity.title})
        except Exception as e:
            results.append({"link": link, "ok": False, "error": str(e)[:80]})

    await db.commit()
    return {"results": results}


@router.patch("/channels/{ch_id}")
async def toggle_react_channel(ch_id: int, db: AsyncSession = Depends(get_db)):
    ch = await db.get(ReactChannel, ch_id)
    if not ch:
        raise HTTPException(404)
    ch.enabled = not ch.enabled
    await db.commit()
    return {"id": ch.id, "enabled": ch.enabled}


@router.patch("/channels/{ch_id}/reaction")
async def set_reaction(ch_id: int, reaction: str, db: AsyncSession = Depends(get_db)):
    ch = await db.get(ReactChannel, ch_id)
    if not ch:
        raise HTTPException(404)
    ch.reaction = reaction
    await db.commit()
    return {"ok": True}


@router.delete("/channels/{ch_id}")
async def delete_react_channel(ch_id: int, db: AsyncSession = Depends(get_db)):
    ch = await db.get(ReactChannel, ch_id)
    if not ch:
        raise HTTPException(404)
    await db.delete(ch)
    await db.commit()
    return {"ok": True}
