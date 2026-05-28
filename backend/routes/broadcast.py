from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncio
from datetime import datetime, timezone, timedelta

from backend.tg_manager import tg_manager

router = APIRouter(prefix="/api/broadcast")

_KYIV = timezone(timedelta(hours=3))


def _ts():
    return datetime.now(_KYIV).strftime('%H:%M:%S')


_state: dict = {
    'status': 'idle',
    'total': 0,
    'sent': 0,
    'failed': 0,
    'log': [],
    '_task': None,
}


class BroadcastRequest(BaseModel):
    contacts: list[str]
    message: str
    account_ids: list[int] = []
    interval: int = 5
    limit_per_account: Optional[int] = None


async def _wait_interval(seconds: int):
    """Wait `seconds` one second at a time, respecting pause/stop state."""
    for _ in range(seconds):
        if _state['status'] == 'stopped':
            return
        while _state['status'] == 'paused':
            await asyncio.sleep(1)
            if _state['status'] == 'stopped':
                return
        await asyncio.sleep(1)


async def _run_broadcast(contacts, message, account_ids, interval, limit_per_account):
    clients = {aid: c for aid, c in tg_manager.clients.items() if aid in account_ids}
    if not clients:
        _state['status'] = 'done'
        return

    # Resolve account display names upfront
    acc_names = {}
    for acc_id, client in clients.items():
        try:
            me = await client.get_me()
            acc_names[acc_id] = me.first_name or me.username or f"#{acc_id}"
        except Exception:
            acc_names[acc_id] = f"#{acc_id}"

    # Build sequential send queue: round-robin by account
    acc_list = list(clients.items())
    sends = []
    for i, contact in enumerate(contacts):
        acc_id = acc_list[i % len(acc_list)][0]
        sends.append((acc_id, contact))

    if limit_per_account:
        counts: dict = {}
        filtered = []
        for acc_id, contact in sends:
            counts[acc_id] = counts.get(acc_id, 0)
            if counts[acc_id] < limit_per_account:
                filtered.append((acc_id, contact))
                counts[acc_id] += 1
        sends = filtered

    _state['total'] = len(sends)

    for i, (acc_id, contact) in enumerate(sends):
        if _state['status'] == 'stopped':
            break
        while _state['status'] == 'paused':
            await asyncio.sleep(1)
            if _state['status'] == 'stopped':
                break

        client = clients[acc_id]
        acc_name = acc_names[acc_id]

        try:
            await client.send_message(contact, message)
            _state['sent'] += 1
            _state['log'].append({'ts': _ts(), 'acc': acc_name, 'contact': contact, 'ok': True})
        except Exception as e:
            from telethon.errors import FloodWaitError
            if isinstance(e, FloodWaitError):
                wait_sec = e.seconds
                mins = wait_sec // 60
                secs = wait_sec % 60
                wait_label = f"{mins}хв {secs}с" if mins else f"{secs}с"
                _state['log'].append({
                    'ts': _ts(), 'acc': acc_name, 'contact': contact,
                    'ok': None, 'err': f'⏳ флуд-ліміт, чекаю {wait_label}...'
                })
                await _wait_interval(wait_sec)
                if _state['status'] == 'stopped':
                    break
                try:
                    await client.send_message(contact, message)
                    _state['sent'] += 1
                    _state['log'].append({'ts': _ts(), 'acc': acc_name, 'contact': contact, 'ok': True})
                except Exception as e2:
                    _state['failed'] += 1
                    _state['log'].append({'ts': _ts(), 'acc': acc_name, 'contact': contact, 'ok': False, 'err': str(e2)[:70]})
            else:
                err_str = str(e)
                if 'PRIVACY_PREMIUM_REQUIRED' in err_str:
                    err_msg = 'тільки для Premium'
                elif 'PEER_FLOOD' in err_str:
                    err_msg = 'ліміт спаму (флуд)'
                elif 'USER_PRIVACY_RESTRICTED' in err_str:
                    err_msg = 'закритий профіль'
                elif 'INPUT_USER_DEACTIVATED' in err_str or 'USER_DEACTIVATED' in err_str:
                    err_msg = 'акаунт видалено'
                elif 'USERNAME_INVALID' in err_str or 'USERNAME_NOT_OCCUPIED' in err_str:
                    err_msg = 'юзернейм не знайдено'
                elif 'Too many requests' in err_str or 'too many' in err_str.lower():
                    err_msg = 'забагато запитів — спробуй збільшити інтервал'
                else:
                    err_msg = err_str[:70]
                _state['failed'] += 1
                _state['log'].append({'ts': _ts(), 'acc': acc_name, 'contact': contact, 'ok': False, 'err': err_msg})

        if len(_state['log']) > 300:
            _state['log'] = _state['log'][-300:]

        if i < len(sends) - 1:
            await _wait_interval(interval)

    if _state['status'] != 'stopped':
        _state['status'] = 'done'


@router.post("/start")
async def start_broadcast(req: BroadcastRequest):
    if _state['status'] in ('running', 'paused'):
        raise HTTPException(400, "Розсилка вже запущена")
    contacts = [c.strip() for c in req.contacts if c.strip()]
    if not contacts:
        raise HTTPException(400, "Список контактів порожній")
    if not req.message.strip():
        raise HTTPException(400, "Повідомлення порожнє")
    account_ids = req.account_ids or list(tg_manager.clients.keys())
    if not account_ids:
        raise HTTPException(400, "Немає підключених акаунтів")

    _state.update({'status': 'running', 'sent': 0, 'failed': 0, 'log': [], 'total': 0})
    task = asyncio.create_task(_run_broadcast(
        contacts, req.message, account_ids,
        max(1, req.interval), req.limit_per_account
    ))
    _state['_task'] = task
    return {"ok": True}


@router.get("/status")
async def get_status():
    return {
        'status': _state['status'],
        'total': _state['total'],
        'sent': _state['sent'],
        'failed': _state['failed'],
        'log': _state['log'][-60:],
    }


@router.post("/pause")
async def pause():
    if _state['status'] != 'running':
        raise HTTPException(400, "Не запущено")
    _state['status'] = 'paused'
    return {"ok": True}


@router.post("/resume")
async def resume():
    if _state['status'] != 'paused':
        raise HTTPException(400, "Не на паузі")
    _state['status'] = 'running'
    return {"ok": True}


@router.post("/stop")
async def stop():
    _state['status'] = 'stopped'
    if _state['_task']:
        _state['_task'].cancel()
        _state['_task'] = None
    return {"ok": True}
