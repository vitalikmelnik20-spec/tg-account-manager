from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncio
from datetime import datetime, timezone, timedelta

from backend.tg_manager import tg_manager

router = APIRouter(prefix="/api/views")

_KYIV = timezone(timedelta(hours=3))


def _ts():
    return datetime.now(_KYIV).strftime('%H:%M:%S')


_channels: list[dict] = []
_account_ids: list[int] = []
_running: bool = False
_last_ids: dict[int, int] = {}
_log: list[dict] = []
_task = None


class ViewsStartRequest(BaseModel):
    channels: list[dict]
    account_ids: list[int]


def _get_any_client():
    for acc_id in _account_ids:
        if acc_id in tg_manager.clients:
            return tg_manager.clients[acc_id]
    return None


async def _get_entity(client, ch: dict):
    from telethon.tl.types import PeerChannel
    try:
        return await client.get_entity(PeerChannel(ch['channel_id']))
    except Exception:
        if ch.get('username'):
            return await client.get_entity(f"@{ch['username']}")
        raise


async def _add_views_batch(ch: dict, msg_ids: list):
    from telethon.tl.functions.messages import GetMessagesViewsRequest
    for acc_id in _account_ids:
        client = tg_manager.clients.get(acc_id)
        if not client:
            continue
        try:
            entity = await _get_entity(client, ch)
            await client(GetMessagesViewsRequest(peer=entity, id=msg_ids, increment=True))
            for mid in msg_ids:
                _log.append({'ts': _ts(), 'channel': ch['title'], 'msg_id': mid, 'ok': True})
        except Exception as e:
            for mid in msg_ids:
                _log.append({'ts': _ts(), 'channel': ch['title'], 'msg_id': mid, 'ok': False, 'err': str(e)[:50]})
        await asyncio.sleep(0.5)
    if len(_log) > 300:
        _log[:] = _log[-300:]


async def _views_loop():
    global _running, _last_ids
    _running = True
    _last_ids = {}

    for ch in list(_channels):
        client = _get_any_client()
        if not client:
            continue
        try:
            entity = await _get_entity(client, ch)
            async for msg in client.iter_messages(entity, limit=1):
                _last_ids[ch['channel_id']] = msg.id
                break
        except Exception:
            _last_ids[ch['channel_id']] = 0

    while _running:
        await asyncio.sleep(60)
        if not _running:
            break
        for ch in list(_channels):
            if not _running:
                break
            cid = ch['channel_id']
            client = _get_any_client()
            if not client:
                continue
            try:
                entity = await _get_entity(client, ch)
                last = _last_ids.get(cid, 0)
                new_ids = []
                async for msg in client.iter_messages(entity, limit=20):
                    if msg.id <= last:
                        break
                    if msg.message or msg.media:
                        new_ids.append(msg.id)
                if new_ids:
                    _last_ids[cid] = max(new_ids)
                    await _add_views_batch(ch, new_ids)
            except Exception as e:
                print(f"[views] {ch['title']}: {e}")

    _running = False


@router.get("/status")
async def get_status():
    return {"running": _running, "log": _log[-60:]}


@router.post("/start")
async def start(req: ViewsStartRequest):
    global _task, _running, _channels, _account_ids
    if _running:
        raise HTTPException(400, "Вже запущено")
    if not req.channels:
        raise HTTPException(400, "Немає каналів для моніторингу")
    if not req.account_ids:
        raise HTTPException(400, "Немає акаунтів")
    _channels = req.channels
    _account_ids = req.account_ids
    _log.clear()
    _task = asyncio.create_task(_views_loop())
    return {"ok": True}


@router.post("/stop")
async def stop():
    global _running, _task
    _running = False
    if _task:
        _task.cancel()
        _task = None
    return {"ok": True}
