from fastapi import APIRouter, HTTPException
from telethon.tl.types import Channel, InputPeerEmpty
from datetime import datetime, timezone, timedelta

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


def _period_start(period: str):
    now = datetime.now(timezone.utc)
    if period == 'day':
        return now - timedelta(days=1)
    elif period == 'week':
        return now - timedelta(weeks=1)
    elif period == 'month':
        return now - timedelta(days=30)
    elif period == 'year':
        return now - timedelta(days=365)
    return None


@router.get("/stats")
async def get_channel_stats(account_id: int, channel_id: int, period: str = 'week'):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")
    try:
        from telethon.tl.types import PeerChannel
        entity = await client.get_entity(PeerChannel(channel_id))
        start = _period_start(period)
        limit_map = {'day': 50, 'week': 200, 'month': 600, 'year': 5000, 'all': None}
        limit = limit_map.get(period, 500)

        UA_MONTHS = {1:'Січ',2:'Лют',3:'Бер',4:'Квіт',5:'Трав',6:'Черв',
                     7:'Лип',8:'Серп',9:'Вер',10:'Жовт',11:'Лист',12:'Груд'}

        posts = []
        daily: dict = {}

        async for msg in client.iter_messages(entity, limit=limit):
            msg_date = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
            if start and msg_date < start:
                break
            if not msg.message and not msg.media:
                continue

            views = msg.views or 0
            fwds = msg.forwards or 0
            react_total = 0
            react_list = []
            if msg.reactions:
                for r in msg.reactions.results:
                    emoji = getattr(r.reaction, 'emoticon', '?')
                    react_list.append({'emoji': emoji, 'count': r.count})
                    react_total += r.count

            d = msg_date
            if period in ('year', 'all'):
                key = f"{UA_MONTHS[d.month]} {str(d.year)[-2:]}"
                sort_key = d.strftime('%Y-%m')
            else:
                key = d.strftime('%d.%m')
                sort_key = d.strftime('%Y-%m-%d')

            if key not in daily:
                daily[key] = {'views': 0, 'reactions': 0, 'forwards': 0, 'posts': 0, '_sk': sort_key}
            daily[key]['views'] += views
            daily[key]['reactions'] += react_total
            daily[key]['forwards'] += fwds
            daily[key]['posts'] += 1

            posts.append({
                'id': msg.id,
                'date': msg_date.isoformat(),
                'text': (msg.message or '')[:120],
                'views': views,
                'forwards': fwds,
                'reactions_total': react_total,
                'reactions': react_list,
                'has_media': bool(msg.media),
            })

        sorted_days = sorted(daily.items(), key=lambda x: x[1]['_sk'])
        chart = [{'label': k, 'views': v['views'], 'reactions': v['reactions'],
                  'forwards': v['forwards'], 'posts': v['posts']} for k, v in sorted_days]

        return {
            'total_views': sum(p['views'] for p in posts),
            'total_reactions': sum(p['reactions_total'] for p in posts),
            'total_forwards': sum(p['forwards'] for p in posts),
            'total_posts': len(posts),
            'chart': chart,
            'posts': posts,
        }
    except Exception as e:
        raise HTTPException(400, f"Помилка: {str(e)[:100]}")
