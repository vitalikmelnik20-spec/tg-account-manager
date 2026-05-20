from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncio
import random
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
    delay_min: int = 30
    delay_max: int = 60
    limit_per_account: Optional[int] = None


async def _send_to_contacts(acc_id: int, client, contacts: list, message: str, delay_min: int, delay_max: int):
    try:
        me = await client.get_me()
        acc_name = me.first_name or me.username or f"#{acc_id}"
    except Exception:
        acc_name = f"#{acc_id}"

    for i, contact in enumerate(contacts):
        if _state['status'] == 'stopped':
            break
        while _state['status'] == 'paused':
            await asyncio.sleep(1)
            if _state['status'] == 'stopped':
                return
        try:
            await client.send_message(contact, message)
            _state['sent'] += 1
            _state['log'].append({'ts': _ts(), 'acc': acc_name, 'contact': contact, 'ok': True})
        except Exception as e:
            _state['failed'] += 1
            _state['log'].append({'ts': _ts(), 'acc': acc_name, 'contact': contact, 'ok': False, 'err': str(e)[:80]})
        if len(_state['log']) > 300:
            _state['log'] = _state['log'][-300:]
        if i < len(contacts) - 1:
            await asyncio.sleep(random.randint(delay_min, delay_max))


async def _run_broadcast(contacts, message, account_ids, delay_min, delay_max, limit_per_account):
    clients = {aid: c for aid, c in tg_manager.clients.items() if aid in account_ids}
    if not clients:
        _state['status'] = 'done'
        return

    acc_list = list(clients.items())
    assignments: dict = {aid: [] for aid in clients}
    for i, contact in enumerate(contacts):
        acc_id = acc_list[i % len(acc_list)][0]
        assignments[acc_id].append(contact)

    if limit_per_account:
        assignments = {aid: clist[:limit_per_account] for aid, clist in assignments.items()}

    _state['total'] = sum(len(v) for v in assignments.values())

    tasks = [
        _send_to_contacts(acc_id, clients[acc_id], clist, message, delay_min, delay_max)
        for acc_id, clist in assignments.items() if clist
    ]
    await asyncio.gather(*tasks)
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
        req.delay_min, req.delay_max, req.limit_per_account
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
