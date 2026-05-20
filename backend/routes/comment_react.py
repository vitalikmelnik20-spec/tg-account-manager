import asyncio
import random
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from backend.database import get_db, AsyncSessionLocal
from backend.models import CommentReactChannel
from backend.tg_manager import tg_manager

router = APIRouter(prefix="/api/comment-react")

_state = {
    "running": False,
    "task": None,
    "log": [],
    "total_reacted": 0,
    "total_errors": 0,
    "account_ids": [],
    "reactor_idx": 0,
}


_KYIV = timezone(timedelta(hours=3))


def _ts():
    return datetime.now(_KYIV).strftime("%H:%M:%S")


async def _broadcast(entry: dict):
    from backend.routes.ws import manager as ws_manager
    _state["log"].append(entry)
    if len(_state["log"]) > 3000:
        _state["log"] = _state["log"][-2500:]
    await ws_manager.broadcast({"type": "comment_react_log", **entry})


async def _push_stats():
    from backend.routes.ws import manager as ws_manager
    await ws_manager.broadcast({
        "type": "comment_react_stats",
        "running": _state["running"],
        "total_reacted": _state["total_reacted"],
        "total_errors": _state["total_errors"],
    })


def _next_reactor() -> Optional[int]:
    """Round-robin через підключені акаунти."""
    available = [a for a in _state["account_ids"] if tg_manager.clients.get(a)]
    if not available:
        return None
    idx = _state["reactor_idx"] % len(available)
    _state["reactor_idx"] = idx + 1
    return available[idx]


def _is_user_comment(msg) -> bool:
    from telethon.tl.types import PeerUser
    return msg.from_id is not None and isinstance(msg.from_id, PeerUser)


async def _resolve_discussion(client, ch: CommentReactChannel) -> Optional[tuple]:
    """Returns (discussion_id, discussion_access_hash) or None."""
    try:
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.types import InputChannel
        full = await client(GetFullChannelRequest(InputChannel(ch.channel_id, ch.access_hash)))
        did = full.full_chat.linked_chat_id
        if not did:
            return None
        d_chat = next((c for c in full.chats if c.id == did), None)
        if d_chat:
            return (did, d_chat.access_hash)
        return (did, 0)
    except Exception:
        return None


async def _get_entity_any_client(link: str, preferred_aid=None):
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


async def _process_channel(ch: CommentReactChannel, poll_client):
    """Перевіряє нові коментарі та ставить реакції по черзі."""
    if not ch.discussion_id:
        result = await _resolve_discussion(poll_client, ch)
        if not result:
            await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⚠  [{ch.title}]  Немає групи обговорень (потрібен увімкнений коментинг)"})
            return
        ch.discussion_id, ch.discussion_hash = result
        async with AsyncSessionLocal() as db:
            db_ch = await db.get(CommentReactChannel, ch.id)
            if db_ch:
                db_ch.discussion_id = ch.discussion_id
                db_ch.discussion_hash = ch.discussion_hash
                await db.commit()

    try:
        from telethon.tl.types import PeerChannel
        disc_entity = await poll_client.get_entity(PeerChannel(ch.discussion_id))

        new_comments = []
        async for msg in poll_client.iter_messages(disc_entity, min_id=ch.last_comment_id, limit=50):
            if _is_user_comment(msg):
                new_comments.append(msg)

        if not new_comments:
            return

        new_comments.sort(key=lambda m: m.id)  # oldest first

        for msg in new_comments:
            if not _state["running"]:
                break
            reactor_id = _next_reactor()
            if not reactor_id:
                await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⚠  Немає доступних акаунтів"})
                break

            reactor_client = tg_manager.clients.get(reactor_id)
            if not reactor_client:
                continue

            try:
                disc_for_reactor = await reactor_client.get_entity(PeerChannel(ch.discussion_id))
            except Exception:
                disc_for_reactor = disc_entity

            try:
                from telethon.tl.functions.messages import SendReactionRequest
                from telethon.tl.types import ReactionEmoji
                await reactor_client(SendReactionRequest(
                    peer=disc_for_reactor,
                    msg_id=msg.id,
                    reaction=[ReactionEmoji(emoticon=ch.reaction)],
                ))
                _state["total_reacted"] += 1
                me = await reactor_client.get_me()
                label = me.first_name or me.username or f"#{reactor_id}"
                await _broadcast({"level": "ok", "msg": f"[{_ts()}] ✓  [{ch.title}]  {ch.reaction}  [{label}]  коментар #{msg.id}"})
            except Exception as e:
                _state["total_errors"] += 1
                await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{ch.title}]  #{msg.id}  {str(e)[:70]}"})

            await asyncio.sleep(random.uniform(1.0, 3.0))

        max_id = max(m.id for m in new_comments)
        ch.last_comment_id = max_id
        async with AsyncSessionLocal() as db:
            db_ch = await db.get(CommentReactChannel, ch.id)
            if db_ch:
                db_ch.last_comment_id = max_id
                await db.commit()

    except Exception as e:
        await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{ch.title}]  {str(e)[:80]}"})


async def _comment_react_loop():
    await _broadcast({"level": "info", "msg": f"[{_ts()}] ▶ РЕАКЦІЇ НА КОМЕНТАРІ СТАРТ  акаунтів: {len(_state['account_ids'])}  інтервал: 30с"})

    while _state["running"]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(CommentReactChannel).where(CommentReactChannel.enabled == True))
            channels = result.scalars().all()

        poll_aid = next(
            (a for a in _state["account_ids"] if tg_manager.clients.get(a)),
            next(iter(tg_manager.clients), None),
        )

        if not poll_aid:
            await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⚠ Немає підключених акаунтів"})
        else:
            poll_client = tg_manager.clients[poll_aid]
            for ch in channels:
                if not _state["running"]:
                    break
                try:
                    await _process_channel(ch, poll_client)
                except Exception as e:
                    await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{ch.title}]  {str(e)[:80]}"})

        await _push_stats()

        for _ in range(30):
            if not _state["running"]:
                break
            await asyncio.sleep(1)

    _state["running"] = False
    await _broadcast({
        "level": "info",
        "msg": f"[{_ts()}] ■ ЗУПИНЕНО  ✓{_state['total_reacted']} реакцій  ✕{_state['total_errors']} помилок",
    })
    await _push_stats()


# ── Routes ──────────────────────────────────────────────

class AddChannelReq(BaseModel):
    link: str
    reaction: str = "👍"
    account_id: Optional[int] = None


class AddChannelsBulkReq(BaseModel):
    links: list[str]
    reaction: str = "👍"
    account_id: Optional[int] = None


class StartReq(BaseModel):
    account_ids: list[int] = []


@router.post("/start")
async def start(req: StartReq):
    if _state["running"]:
        return {"error": "Вже запущено"}
    _state["running"] = True
    _state["log"] = []
    _state["total_reacted"] = 0
    _state["total_errors"] = 0
    _state["reactor_idx"] = 0
    _state["account_ids"] = req.account_ids or list(tg_manager.clients.keys())
    _state["task"] = asyncio.create_task(_comment_react_loop())
    return {"ok": True}


@router.post("/stop")
async def stop():
    _state["running"] = False
    task = _state.get("task")
    if task and not task.done():
        task.cancel()
    return {"ok": True}


@router.get("/status")
async def get_status():
    return {
        "running": _state["running"],
        "total_reacted": _state["total_reacted"],
        "total_errors": _state["total_errors"],
        "account_ids": _state["account_ids"],
        "log": _state["log"][-500:],
    }


@router.get("/channels")
async def list_channels(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CommentReactChannel).order_by(CommentReactChannel.created_at))
    return [
        {
            "id": ch.id,
            "title": ch.title,
            "username": ch.username,
            "channel_id": ch.channel_id,
            "reaction": ch.reaction,
            "last_comment_id": ch.last_comment_id,
            "enabled": ch.enabled,
            "has_discussion": bool(ch.discussion_id),
        }
        for ch in result.scalars().all()
    ]


@router.post("/channels")
async def add_channel(req: AddChannelReq, db: AsyncSession = Depends(get_db)):
    if not tg_manager.clients:
        raise HTTPException(400, "Немає підключених акаунтів")
    try:
        entity = await _get_entity_any_client(req.link.strip(), req.account_id)

        # Resolve discussion group immediately
        discussion_id = discussion_hash = None
        try:
            preferred = tg_manager.clients.get(req.account_id) or next(iter(tg_manager.clients.values()))
            from telethon.tl.functions.channels import GetFullChannelRequest
            from telethon.tl.types import InputChannel
            full = await preferred(GetFullChannelRequest(InputChannel(entity.id, entity.access_hash)))
            did = full.full_chat.linked_chat_id
            if did:
                discussion_id = did
                d_chat = next((c for c in full.chats if c.id == did), None)
                if d_chat:
                    discussion_hash = d_chat.access_hash
        except Exception:
            pass

        ch = CommentReactChannel(
            channel_id=entity.id,
            access_hash=entity.access_hash,
            discussion_id=discussion_id,
            discussion_hash=discussion_hash,
            username=getattr(entity, "username", None),
            title=entity.title,
            reaction=req.reaction or "👍",
            last_comment_id=0,
            enabled=True,
        )
        db.add(ch)
        await db.commit()
        await db.refresh(ch)
        return {"id": ch.id, "title": ch.title, "has_discussion": bool(discussion_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Не вдалось знайти канал: {e}")


@router.post("/channels/bulk")
async def add_channels_bulk(req: AddChannelsBulkReq, db: AsyncSession = Depends(get_db)):
    if not tg_manager.clients:
        raise HTTPException(400, "Немає підключених акаунтів")
    existing = {r for r in (await db.execute(select(CommentReactChannel.channel_id))).scalars()}
    results = []
    preferred = tg_manager.clients.get(req.account_id) or next(iter(tg_manager.clients.values()), None)
    for link in req.links:
        link = link.strip()
        if not link:
            continue
        try:
            entity = await _get_entity_any_client(link, req.account_id)
            if entity.id in existing:
                results.append({"link": link, "ok": False, "error": "вже додано"})
                continue

            discussion_id = discussion_hash = None
            if preferred:
                try:
                    from telethon.tl.functions.channels import GetFullChannelRequest
                    from telethon.tl.types import InputChannel
                    full = await preferred(GetFullChannelRequest(InputChannel(entity.id, entity.access_hash)))
                    did = full.full_chat.linked_chat_id
                    if did:
                        discussion_id = did
                        d_chat = next((c for c in full.chats if c.id == did), None)
                        if d_chat:
                            discussion_hash = d_chat.access_hash
                except Exception:
                    pass

            db.add(CommentReactChannel(
                channel_id=entity.id,
                access_hash=entity.access_hash,
                discussion_id=discussion_id,
                discussion_hash=discussion_hash,
                username=getattr(entity, "username", None),
                title=entity.title,
                reaction=req.reaction or "👍",
                last_comment_id=0,
                enabled=True,
            ))
            existing.add(entity.id)
            results.append({"link": link, "ok": True, "title": entity.title, "has_discussion": bool(discussion_id)})
        except Exception as e:
            results.append({"link": link, "ok": False, "error": str(e)[:80]})
    await db.commit()
    return {"results": results}


@router.patch("/channels/{ch_id}")
async def toggle_channel(ch_id: int, db: AsyncSession = Depends(get_db)):
    ch = await db.get(CommentReactChannel, ch_id)
    if not ch:
        raise HTTPException(404)
    ch.enabled = not ch.enabled
    await db.commit()
    return {"id": ch.id, "enabled": ch.enabled}


@router.patch("/channels/{ch_id}/reaction")
async def set_reaction(ch_id: int, reaction: str, db: AsyncSession = Depends(get_db)):
    ch = await db.get(CommentReactChannel, ch_id)
    if not ch:
        raise HTTPException(404)
    ch.reaction = reaction
    await db.commit()
    return {"ok": True}


@router.delete("/channels/{ch_id}")
async def delete_channel(ch_id: int, db: AsyncSession = Depends(get_db)):
    ch = await db.get(CommentReactChannel, ch_id)
    if not ch:
        raise HTTPException(404)
    await db.delete(ch)
    await db.commit()
    return {"ok": True}
