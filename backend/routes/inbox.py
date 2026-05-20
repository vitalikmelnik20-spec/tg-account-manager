from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from telethon.tl.types import User

from backend.tg_manager import tg_manager

router = APIRouter(prefix="/api/inbox")


@router.get("/dialogs")
async def get_dialogs(account_id: int, limit: int = 80):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")
    try:
        dialogs = []
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity
            if not isinstance(entity, User):
                continue
            if getattr(entity, 'bot', False) or getattr(entity, 'is_self', False):
                continue
            last_msg = dialog.message
            dialogs.append({
                'id': entity.id,
                'name': (f"{entity.first_name or ''} {entity.last_name or ''}".strip()
                         or entity.username or f"#{entity.id}"),
                'username': entity.username,
                'last_message': (last_msg.message or '')[:60] if last_msg else '',
                'last_date': last_msg.date.isoformat() if last_msg and last_msg.date else None,
                'unread_count': dialog.unread_count,
            })
        return dialogs
    except Exception as e:
        raise HTTPException(400, f"Помилка: {str(e)[:100]}")


@router.get("/messages")
async def get_messages(account_id: int, peer_id: int, limit: int = 60):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")
    try:
        from telethon.tl.types import PeerUser
        entity = await client.get_entity(PeerUser(peer_id))
        msgs = []
        async for msg in client.iter_messages(entity, limit=limit):
            msgs.append({
                'id': msg.id,
                'text': msg.message or '',
                'date': msg.date.isoformat() if msg.date else None,
                'out': msg.out,
                'has_media': bool(msg.media),
            })
        return list(reversed(msgs))
    except Exception as e:
        raise HTTPException(400, f"Помилка: {str(e)[:100]}")


class ReplyBody(BaseModel):
    account_id: int
    peer_id: int
    text: str


@router.post("/send")
async def send_reply(body: ReplyBody):
    client = tg_manager.clients.get(body.account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")
    if not body.text.strip():
        raise HTTPException(400, "Текст порожній")
    try:
        from telethon.tl.types import PeerUser
        entity = await client.get_entity(PeerUser(body.peer_id))
        await client.send_message(entity, body.text)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, f"Помилка: {str(e)[:100]}")
