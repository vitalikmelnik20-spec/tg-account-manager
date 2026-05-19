from fastapi import APIRouter, HTTPException
from telethon.tl.types import Channel, InputPeerEmpty

from backend.tg_manager import tg_manager

router = APIRouter(prefix="/api/mychannels")


async def _collect_admin_channels(account_id: int, client) -> list[dict]:
    channels = []
    try:
        me = await client.get_me()
        acc_name = me.first_name or me.username or f"#{account_id}"
        async for dialog in client.iter_dialogs(limit=500):
            entity = dialog.entity
            if not isinstance(entity, Channel) or not entity.broadcast or entity.left:
                continue
            if entity.creator or entity.admin_rights:
                channels.append({
                    "channel_id": entity.id,
                    "access_hash": entity.access_hash,
                    "title": entity.title,
                    "username": entity.username,
                    "members_count": getattr(entity, "participants_count", None),
                    "account_id": account_id,
                    "account_name": acc_name,
                })
    except Exception as e:
        print(f"[mychannels] account {account_id}: {e}")
    return channels


@router.get("")
async def get_my_channels():
    all_channels: list[dict] = []
    seen: set = set()
    for account_id, client in list(tg_manager.clients.items()):
        for ch in await _collect_admin_channels(account_id, client):
            key = (account_id, ch["channel_id"])
            if key not in seen:
                seen.add(key)
                all_channels.append(ch)
    return all_channels


@router.get("/posts")
async def get_channel_posts(account_id: int, channel_id: int, limit: int = 20):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")
    try:
        from telethon.tl.types import PeerChannel
        entity = await client.get_entity(PeerChannel(channel_id))
        posts = []
        async for msg in client.iter_messages(entity, limit=limit):
            reactions_list = []
            reactions_total = 0
            if msg.reactions:
                for r in msg.reactions.results:
                    emoji = getattr(r.reaction, "emoticon", "?")
                    reactions_list.append({"emoji": emoji, "count": r.count})
                    reactions_total += r.count
            posts.append({
                "id": msg.id,
                "date": msg.date.isoformat() if msg.date else None,
                "text": (msg.message or "")[:200],
                "views": msg.views or 0,
                "forwards": msg.forwards or 0,
                "reactions_total": reactions_total,
                "reactions": reactions_list,
                "has_media": bool(msg.media),
            })
        return posts
    except Exception as e:
        raise HTTPException(400, f"Помилка: {str(e)[:100]}")


@router.get("/forwards")
async def get_post_forwards(account_id: int, channel_id: int, msg_id: int):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")
    try:
        from telethon.tl.types import PeerChannel
        from telethon.tl.functions.stats import GetMessagePublicForwardsRequest
        entity = await client.get_entity(PeerChannel(channel_id))
        result = await client(GetMessagePublicForwardsRequest(
            channel=entity,
            msg_id=msg_id,
            offset_rate=0,
            offset_peer=InputPeerEmpty(),
            offset_id=0,
            limit=50,
        ))
        forwards = []
        for msg in result.messages:
            try:
                from_entity = await client.get_entity(msg.peer_id)
                forwards.append({
                    "title": getattr(from_entity, "title", "Unknown"),
                    "username": getattr(from_entity, "username", None),
                    "views": getattr(msg, "views", 0) or 0,
                    "date": msg.date.isoformat() if msg.date else None,
                })
            except Exception:
                pass
        return forwards
    except Exception as e:
        raise HTTPException(400, f"Репости недоступні: {str(e)[:80]}")
