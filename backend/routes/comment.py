import asyncio
import random
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from backend.database import get_db, AsyncSessionLocal
from backend.models import MonitoredChannel
from backend.tg_manager import tg_manager

router = APIRouter(prefix="/api/comment")

import os
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4.1-mini")

_state = {
    "running": False,
    "task": None,
    "log": [],
    "total_comments": 0,
    "total_skipped": 0,
    "total_errors": 0,
}
_last_used: dict[int, int] = {}  # channel_id -> account_id last used


_KYIV = timezone(timedelta(hours=3))


def _ts():
    return datetime.now(_KYIV).strftime("%H:%M:%S")


async def _broadcast(entry: dict):
    from backend.routes.ws import manager as ws_manager
    _state["log"].append(entry)
    if len(_state["log"]) > 5000:
        _state["log"] = _state["log"][-4000:]
    await ws_manager.broadcast({"type": "comment_log", **entry})


async def _push_stats():
    from backend.routes.ws import manager as ws_manager
    await ws_manager.broadcast({
        "type": "comment_stats",
        "running": _state["running"],
        "total_comments": _state["total_comments"],
        "total_skipped": _state["total_skipped"],
        "total_errors": _state["total_errors"],
    })


async def _generate_comment(post_text: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Ти звичайний читач українського Telegram каналу. "
                            "Пиши короткі природні коментарі (1-2 речення) до постів. "
                            "Пиши лише українською. Без хештегів, без емодзі, без зайвих фраз."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Напиши коментар до цього поста:\n\n{post_text[:600]}",
                    },
                ],
            },
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


def _pick_account(channel_id: int) -> Optional[int]:
    pool = list(tg_manager.clients.keys())
    if not pool:
        return None
    last = _last_used.get(channel_id)
    preferred = [a for a in pool if a != last]
    chosen = random.choice(preferred if preferred else pool)
    _last_used[channel_id] = chosen
    return chosen


async def _acc_label(client, account_id: int) -> str:
    try:
        me = await client.get_me()
        name = me.first_name or f"#{account_id}"
        return f"{name} (@{me.username})" if me.username else name
    except Exception:
        return f"#{account_id}"


async def _comment_on_post(client, entity, post, ch: MonitoredChannel, account_id: int):
    from telethon.tl.functions.messages import GetDiscussionMessageRequest

    text = post.message or ""
    if len(text) < 30:
        await _broadcast({
            "level": "warn",
            "msg": f"[{_ts()}] ⚠  [{ch.title}]  пост #{post.id} замалий ({len(text)} симв.) — пропускаємо",
        })
        _state["total_skipped"] += 1
        return

    await _broadcast({
        "level": "info",
        "msg": f"[{_ts()}] ⏳  [{ch.title}]  новий пост #{post.id} — чекаємо 20с...",
    })
    await asyncio.sleep(20)
    if not _state["running"]:
        return

    try:
        comment_text = await _generate_comment(text)
    except Exception as e:
        await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{ch.title}]  OpenRouter: {str(e)[:80]}"})
        _state["total_errors"] += 1
        return

    try:
        discussion = await client(GetDiscussionMessageRequest(peer=entity, msg_id=post.id))
        disc_peer = discussion.chats[0]
        disc_msg_id = discussion.messages[0].id
        await client.send_message(disc_peer, comment_text, reply_to=disc_msg_id)

        link = f"https://t.me/{ch.username}/{post.id}" if ch.username else f"https://t.me/c/{ch.channel_id}/{post.id}"
        label = await _acc_label(client, account_id)

        short_post = text[:70].replace("\n", " ")
        short_comment = comment_text[:70]

        _state["total_comments"] += 1
        await _broadcast({
            "level": "ok",
            "msg": (
                f"[{_ts()}] ✓  [{ch.title}]  [{label}]\n"
                f"    📰 «{short_post}»\n"
                f"    💬 «{short_comment}»"
            ),
            "link": link,
        })
    except Exception as e:
        await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{ch.title}]  надсилання: {str(e)[:100]}"})
        _state["total_errors"] += 1


async def _poll_channel(client, ch: MonitoredChannel, account_id: int):
    from telethon.tl.types import InputPeerChannel

    try:
        entity = await client.get_entity(InputPeerChannel(ch.channel_id, ch.access_hash))
    except Exception as e:
        await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{ch.title}]  не знайдено: {str(e)[:60]}"})
        return

    new_msgs = []
    async for msg in client.iter_messages(entity, limit=10):
        if msg.id <= ch.last_msg_id:
            break
        if msg.message:
            new_msgs.append(msg)

    if not new_msgs:
        return

    ch.last_msg_id = max(m.id for m in new_msgs)
    newest = max(new_msgs, key=lambda m: m.id)
    await _comment_on_post(client, entity, newest, ch, account_id)


async def _init_last_ids(db):
    result = await db.execute(
        select(MonitoredChannel).where(
            MonitoredChannel.last_msg_id == 0,
            MonitoredChannel.enabled == True,
        )
    )
    for ch in result.scalars().all():
        aid = _pick_account(ch.channel_id)
        if not aid:
            continue
        client = tg_manager.clients.get(aid)
        if not client:
            continue
        try:
            from telethon.tl.types import InputPeerChannel
            entity = await client.get_entity(InputPeerChannel(ch.channel_id, ch.access_hash))
            async for msg in client.iter_messages(entity, limit=1):
                ch.last_msg_id = msg.id
                await _broadcast({"level": "info", "msg": f"[{_ts()}] ℹ  [{ch.title}]  стартова позиція: пост #{msg.id}"})
                break
        except Exception as e:
            await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⚠  [{ch.title}]  старт: {str(e)[:60]}"})
    await db.commit()


async def _comment_loop():
    await _broadcast({"level": "info", "msg": f"[{_ts()}] ▶ АВТО-КОМЕНТИНГ СТАРТ  чекаємо 30с..."})
    await asyncio.sleep(30)

    async with AsyncSessionLocal() as db:
        await _init_last_ids(db)

    await _broadcast({"level": "info", "msg": f"[{_ts()}] 🔄 Опитування каналів кожні 60с"})

    while _state["running"]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(MonitoredChannel).where(MonitoredChannel.enabled == True)
            )
            channels = result.scalars().all()

            for ch in channels:
                if not _state["running"]:
                    break
                aid = _pick_account(ch.channel_id)
                if not aid:
                    await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⚠  [{ch.title}]  немає акаунтів"})
                    continue
                client = tg_manager.clients.get(aid)
                if not client:
                    continue
                try:
                    await _poll_channel(client, ch, aid)
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
        "msg": f"[{_ts()}] ■ ЗУПИНЕНО  💬{_state['total_comments']}  ⚠{_state['total_skipped']}  ✕{_state['total_errors']}",
    })
    await _push_stats()


# ── Routes ──────────────────────────────────────────────

class AddChannelReq(BaseModel):
    link: str
    account_id: Optional[int] = None


class AddChannelsBulkReq(BaseModel):
    links: list[str]
    account_id: Optional[int] = None


@router.post("/start")
async def comment_start():
    if _state["running"]:
        return {"error": "Вже запущено"}
    _state["running"] = True
    _state["log"] = []
    _state["total_comments"] = 0
    _state["total_skipped"] = 0
    _state["total_errors"] = 0
    _state["task"] = asyncio.create_task(_comment_loop())
    return {"ok": True}


@router.post("/stop")
async def comment_stop():
    _state["running"] = False
    task = _state.get("task")
    if task and not task.done():
        task.cancel()
    return {"ok": True}


@router.get("/status")
async def comment_status():
    return {
        "running": _state["running"],
        "total_comments": _state["total_comments"],
        "total_skipped": _state["total_skipped"],
        "total_errors": _state["total_errors"],
        "log": _state["log"][-500:],
    }


@router.get("/channels")
async def list_channels(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MonitoredChannel).order_by(MonitoredChannel.created_at))
    return [
        {
            "id": ch.id,
            "title": ch.title,
            "username": ch.username,
            "channel_id": ch.channel_id,
            "account_id": ch.account_id,
            "last_msg_id": ch.last_msg_id,
            "enabled": ch.enabled,
        }
        for ch in result.scalars().all()
    ]


@router.post("/channels")
async def add_channel(req: AddChannelReq, db: AsyncSession = Depends(get_db)):
    aid = req.account_id or next(iter(tg_manager.clients), None)
    if not aid:
        raise HTTPException(400, "Немає підключених акаунтів")
    client = tg_manager.clients.get(aid)
    if not client:
        raise HTTPException(400, f"Акаунт #{aid} не підключено")
    try:
        entity = await client.get_entity(req.link.strip())
        ch = MonitoredChannel(
            account_id=aid,
            channel_id=entity.id,
            access_hash=entity.access_hash,
            username=getattr(entity, "username", None),
            title=entity.title,
            last_msg_id=0,
            enabled=True,
        )
        db.add(ch)
        await db.commit()
        await db.refresh(ch)
        return {"id": ch.id, "title": ch.title, "channel_id": ch.channel_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Не вдалось знайти канал: {e}")


@router.post("/channels/bulk")
async def add_channels_bulk(req: AddChannelsBulkReq, db: AsyncSession = Depends(get_db)):
    aid = req.account_id or next(iter(tg_manager.clients), None)
    if not aid:
        raise HTTPException(400, "Немає підключених акаунтів")
    client = tg_manager.clients.get(aid)
    if not client:
        raise HTTPException(400, f"Акаунт #{aid} не підключено")

    # get already monitored channel_ids to skip duplicates
    existing = await db.execute(select(MonitoredChannel.channel_id))
    existing_ids = {r for r in existing.scalars()}

    results = []
    for link in req.links:
        link = link.strip()
        if not link:
            continue
        try:
            entity = await client.get_entity(link)
            if entity.id in existing_ids:
                results.append({"link": link, "ok": False, "error": "вже додано"})
                continue
            ch = MonitoredChannel(
                account_id=aid,
                channel_id=entity.id,
                access_hash=entity.access_hash,
                username=getattr(entity, "username", None),
                title=entity.title,
                last_msg_id=0,
                enabled=True,
            )
            db.add(ch)
            existing_ids.add(entity.id)
            results.append({"link": link, "ok": True, "title": entity.title})
        except Exception as e:
            results.append({"link": link, "ok": False, "error": str(e)[:80]})

    await db.commit()
    return {"results": results}


@router.patch("/channels/{ch_id}")
async def toggle_channel(ch_id: int, db: AsyncSession = Depends(get_db)):
    ch = await db.get(MonitoredChannel, ch_id)
    if not ch:
        raise HTTPException(404, "Канал не знайдено")
    ch.enabled = not ch.enabled
    await db.commit()
    return {"id": ch.id, "enabled": ch.enabled}


@router.delete("/channels/{ch_id}")
async def delete_channel(ch_id: int, db: AsyncSession = Depends(get_db)):
    ch = await db.get(MonitoredChannel, ch_id)
    if not ch:
        raise HTTPException(404)
    await db.delete(ch)
    await db.commit()
    return {"ok": True}
