import json as json_module
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from telethon.tl.types import Channel, InputPeerEmpty
from datetime import datetime, timezone, timedelta

from backend.tg_manager import tg_manager
from backend.database import get_db, AsyncSessionLocal
from backend.models import ChannelMembersHistory

router = APIRouter(prefix="/api/mychannels")

_KYIV = timezone(timedelta(hours=3))
_UA_MONTHS = {1:'Січ',2:'Лют',3:'Бер',4:'Квіт',5:'Трав',6:'Черв',
               7:'Лип',8:'Серп',9:'Вер',10:'Жовт',11:'Лист',12:'Груд'}

# Minimum gap between saved snapshots for the same channel (30 minutes)
_SNAPSHOT_MIN_GAP = timedelta(minutes=30)


async def _save_member_snapshot(db: AsyncSession, account_id: int, channel_id: int, count: int):
    """Save subscriber snapshot with deduplication: skip if count unchanged and < 30 min passed."""
    last_q = await db.execute(
        select(ChannelMembersHistory)
        .where(ChannelMembersHistory.account_id == account_id)
        .where(ChannelMembersHistory.channel_id == channel_id)
        .order_by(ChannelMembersHistory.recorded_at.desc())
        .limit(1)
    )
    last = last_q.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if last:
        last_at = last.recorded_at if last.recorded_at.tzinfo else last.recorded_at.replace(tzinfo=timezone.utc)
        if last.members_count == count and (now - last_at) < _SNAPSHOT_MIN_GAP:
            return  # No change, too recent — skip
    db.add(ChannelMembersHistory(account_id=account_id, channel_id=channel_id, members_count=count))


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


async def collect_all_snapshots():
    """Collect and save member count snapshots for all admin channels. Called by background task."""
    async with AsyncSessionLocal() as db:
        for account_id, client in list(tg_manager.clients.items()):
            try:
                for ch in await _collect_admin_channels(account_id, client):
                    if ch["members_count"]:
                        await _save_member_snapshot(db, account_id, ch["channel_id"], ch["members_count"])
                await db.commit()
            except Exception as e:
                print(f"[snapshot] account {account_id}: {e}")


@router.get("")
async def get_my_channels(db: AsyncSession = Depends(get_db)):
    all_channels: list[dict] = []
    seen: set = set()
    for account_id, client in list(tg_manager.clients.items()):
        for ch in await _collect_admin_channels(account_id, client):
            key = (account_id, ch["channel_id"])
            if key not in seen:
                seen.add(key)
                all_channels.append(ch)
                if ch["members_count"]:
                    await _save_member_snapshot(db, account_id, ch["channel_id"], ch["members_count"])
    await db.commit()
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


_UA_MONTHS_NOM = {
    1: 'Січень', 2: 'Лютий', 3: 'Березень', 4: 'Квітень',
    5: 'Травень', 6: 'Червень', 7: 'Липень', 8: 'Серпень',
    9: 'Вересень', 10: 'Жовтень', 11: 'Листопад', 12: 'Грудень',
}
_WD_UA = {5: 'Сб', 6: 'Нд'}


def _period_start(period: str):
    """Keep for subscriber-stats (backward compat)."""
    now = datetime.now(_KYIV)
    if period == 'day':
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'week':
        monday = now - timedelta(days=now.weekday())
        return monday.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'month':
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == 'year':
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def _period_range(period: str, offset: int = 0):
    """Return (start_utc, end_utc, label) for the given period + offset.
    offset=0 → current period, offset=-1 → one period back, etc.
    """
    now = datetime.now(_KYIV)
    today = now.date()

    if period == 'day':
        target = today + timedelta(days=offset)
        start = datetime.combine(target, datetime.min.time()).replace(tzinfo=_KYIV)
        end = start + timedelta(days=1)
        if offset == 0:   label = 'Сьогодні'
        elif offset == -1: label = 'Вчора'
        else:              label = target.strftime('%d.%m.%Y')

    elif period == 'week':
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
        start = datetime.combine(monday, datetime.min.time()).replace(tzinfo=_KYIV)
        end = start + timedelta(days=7)
        if offset == 0:   label = 'Цей тиждень'
        elif offset == -1: label = 'Мин. тиждень'
        else:
            sunday = monday + timedelta(days=6)
            label = f'{monday.strftime("%d.%m")}–{sunday.strftime("%d.%m")}'

    elif period == 'month':
        m, y = today.month + offset, today.year
        while m <= 0:  m += 12; y -= 1
        while m > 12: m -= 12; y += 1
        start = datetime(y, m, 1, tzinfo=_KYIV)
        nm, ny = (m % 12 + 1), (y + (1 if m == 12 else 0))
        end = datetime(ny, nm, 1, tzinfo=_KYIV)
        if offset == 0: label = 'Цей місяць'
        else:           label = f'{_UA_MONTHS_NOM.get(m, str(m))} {y}'

    elif period == 'year':
        y = today.year + offset
        start = datetime(y, 1, 1, tzinfo=_KYIV)
        end = datetime(y + 1, 1, 1, tzinfo=_KYIV)
        label = str(y)

    elif period == 'weekend':
        # last Saturday, stepping back by `offset` weeks (offset <= 0)
        days_since_sat = (today.weekday() - 5) % 7
        sat = today - timedelta(days=days_since_sat) + timedelta(weeks=offset)
        sun = sat + timedelta(days=1)
        start = datetime.combine(sat, datetime.min.time()).replace(tzinfo=_KYIV)
        end = start + timedelta(days=2)
        if offset == 0:   label = f'Цей вихідний ({sat.strftime("%d.%m")}–{sun.strftime("%d.%m")})'
        elif offset == -1: label = f'Мин. вихідний ({sat.strftime("%d.%m")}–{sun.strftime("%d.%m")})'
        else:              label = f'{sat.strftime("%d.%m")}–{sun.strftime("%d.%m")}'

    else:  # 'all'
        return None, None, 'Весь час'

    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), label


@router.get("/stats")
async def get_channel_stats(account_id: int, channel_id: int, period: str = 'week', offset: int = 0):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")
    try:
        from telethon.tl.types import PeerChannel
        entity = await client.get_entity(PeerChannel(channel_id))

        start_utc, end_utc, label = _period_range(period, offset)

        posts = []
        daily: dict = {}

        async for msg in client.iter_messages(entity, limit=None):
            msg_date = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
            # Skip messages after period end (needed for historical periods)
            if end_utc and msg_date >= end_utc:
                continue
            # Stop when we pass the start of the period
            if start_utc and msg_date < start_utc:
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

            d = msg_date.astimezone(_KYIV)

            if period == 'day':
                key = d.strftime('%H:00')
                sort_key = d.strftime('%H')
            elif period == 'weekend':
                key = f"{_WD_UA.get(d.weekday(), '?')} {d.strftime('%d.%m')}"
                sort_key = d.strftime('%Y-%m-%d')
            elif period in ('year', 'all'):
                key = f"{_UA_MONTHS[d.month]} {str(d.year)[-2:]}"
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
                'text': msg.message or '',
                'views': views,
                'forwards': fwds,
                'reactions_total': react_total,
                'reactions': react_list,
                'has_media': bool(msg.media),
                'comments': msg.replies.replies if msg.replies else 0,
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
            'label': label,
            'offset': offset,
        }
    except Exception as e:
        raise HTTPException(400, f"Помилка: {str(e)[:100]}")


# ── Subscriber stats helpers ──────────────────────────────────────────────────

def _parse_tg_graph(graph_data: dict):
    """Parse Telegram chart.js JSON into (entries, col_keys, names_map).
    names_map: {'y0': 'Search', 'y1': 'Invites', ...}
    """
    cols = graph_data.get('columns', [])
    names_map = graph_data.get('names', {})   # e.g. {'y0': 'Search'}
    x_col = next((c for c in cols if c[0] == 'x'), None)
    if not x_col:
        return [], [], {}
    ts_list = x_col[1:]
    data_cols = [(c[0], c[1:]) for c in cols if c[0] != 'x']
    entries = []
    for i, ts in enumerate(ts_list):
        entry = {'_ts': ts}
        for name, vals in data_cols:
            entry[name] = vals[i] if i < len(vals) else 0
        entries.append(entry)
    col_names = [n for n, _ in data_cols]
    return entries, col_names, names_map


def _filter_by_period(entries: list, period: str, offset: int = 0) -> list:
    if period == 'weekend':
        start_utc, end_utc, _ = _period_range('weekend', offset)
        if not start_utc:
            return entries
        start_ms = int(start_utc.timestamp() * 1000)
        end_ms   = int(end_utc.timestamp() * 1000)
        return [e for e in entries if start_ms <= e['_ts'] < end_ms]
    start = _period_start(period)
    if not start:
        return entries
    start_ms = int(start.timestamp() * 1000)
    return [e for e in entries if e['_ts'] >= start_ms]


def _ts_label(ts_ms: int, period: str) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, _KYIV)
    if period == 'day':
        return dt.strftime('%H:00')
    if period in ('year', 'all'):
        return f"{_UA_MONTHS[dt.month]} {str(dt.year)[-2:]}"
    return dt.strftime('%d.%m')


_SOURCE_NAMES = {
    'Invites': 'Запрошення',
    'Search': 'Пошук',
    'Others': 'Інше',
    'External': 'Зовнішні джерела',
    'Mentions': 'Згадки',
    'Forwards': 'Репости',
    'Recommended': 'Рекомендації',
    'Broadcast': 'Розсилки',
    'Stickers': 'Стікерпаки',
    'Giveaway': 'Розіграші',
}


async def _resolve_graph(sender_call, graph):
    """Resolve StatsGraph/StatsGraphAsync → parsed dict, using provided sender_call."""
    from telethon.tl.types import StatsGraph, StatsGraphAsync, StatsGraphError
    from telethon.tl.functions.stats import LoadAsyncGraphRequest
    if isinstance(graph, StatsGraphError):
        return None
    if isinstance(graph, StatsGraphAsync):
        graph = await sender_call(LoadAsyncGraphRequest(token=graph.token))
    if isinstance(graph, StatsGraph):
        return json_module.loads(graph.json.data)
    return None


@router.get("/subscriber-stats")
async def get_subscriber_stats(
    account_id: int,
    channel_id: int,
    period: str = 'week',
    db: AsyncSession = Depends(get_db),
):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")

    try:
        from telethon.tl.types import PeerChannel
        from telethon.tl.functions.stats import GetBroadcastStatsRequest
        from telethon.tl.functions.channels import GetFullChannelRequest

        entity = await client.get_entity(PeerChannel(channel_id))
        current_members = getattr(entity, 'participants_count', None)

        growth_chart: list = []
        followers_chart: list = []
        sources: list = []
        tg_stats_ok = False
        tg_error = ''

        # ── Try Telegram's native Stats API ──────────────────────────────
        import re as _re

        async def _try_stats(call_fn):
            """Fetch and parse all stat graphs using the provided call function."""
            nonlocal tg_stats_ok, growth_chart, followers_chart, sources
            stats = await call_fn(GetBroadcastStatsRequest(channel=entity, dark=False))
            tg_stats_ok = True

            growth_raw = await _resolve_graph(call_fn, stats.growth_graph)
            fol_raw    = await _resolve_graph(call_fn, stats.followers_graph)
            src_raw    = await _resolve_graph(call_fn, stats.new_followers_by_source_graph)

            if growth_raw:
                entries, col_names, _ = _parse_tg_graph(growth_raw)
                entries = _filter_by_period(entries, period)
                y_col = col_names[0] if col_names else None
                for e in entries:
                    growth_chart.append({'label': _ts_label(e['_ts'], period), 'members': e.get(y_col, 0) if y_col else 0})

            if fol_raw:
                entries, col_names, fol_names = _parse_tg_graph(fol_raw)
                entries = _filter_by_period(entries, period)
                # Map y-keys to human names to find joined/left columns
                named = {fol_names.get(k, k).lower(): k for k in col_names}
                jk = next((col_names[i] for i, n in enumerate(col_names)
                           if 'unfollow' not in fol_names.get(n, n).lower()
                           and 'follow' in fol_names.get(n, n).lower()), col_names[0] if col_names else None)
                lk = next((col_names[i] for i, n in enumerate(col_names)
                           if 'unfollow' in fol_names.get(n, n).lower()), col_names[1] if len(col_names) > 1 else None)
                for e in entries:
                    followers_chart.append({'label': _ts_label(e['_ts'], period),
                                            'joined': e.get(jk, 0) if jk else 0,
                                            'left':   e.get(lk, 0) if lk else 0,
                                            'net':   (e.get(jk, 0) if jk else 0) - (e.get(lk, 0) if lk else 0)})

            if src_raw:
                entries, col_names, src_names = _parse_tg_graph(src_raw)
                entries = _filter_by_period(entries, period)
                totals: dict = {}
                for e in entries:
                    for k in col_names:
                        totals[k] = totals.get(k, 0) + e.get(k, 0)
                sources = sorted(
                    [{'source': _SOURCE_NAMES.get(src_names.get(k, k), src_names.get(k, k)), 'count': v}
                     for k, v in totals.items() if v > 0],
                    key=lambda x: x['count'], reverse=True
                )

        try:
            # Step 1: find the stats DC
            full_ch = await client(GetFullChannelRequest(entity))
            stats_dc = getattr(full_ch.full_chat, 'stats_dc', None)
            print(f"[stats] channel {channel_id} stats_dc={stats_dc}")

            if stats_dc:
                sender = await client._borrow_exported_sender(stats_dc)
                await _try_stats(lambda req: client._call(sender, req))
            else:
                await _try_stats(lambda req: client(req))

        except Exception as err:
            err_str = str(err)
            tg_error = err_str[:200]
            print(f"[stats] error for channel {channel_id}: {err_str}")

            # Retry on a different DC if error contains migrate hint
            m = _re.search(r'MIGRATE_(\d+)|migrate.*?(\d+)', err_str, _re.I)
            if m and not tg_stats_ok:
                dc_id = int(m.group(1) or m.group(2))
                try:
                    sender = await client._borrow_exported_sender(dc_id)
                    await _try_stats(lambda req: client._call(sender, req))
                    tg_error = ''
                except Exception as err2:
                    tg_error = f"DC{dc_id}: {str(err2)[:180]}"

        # ── DB historical fallback (always collected, used when TG stats unavailable) ──
        hist_q = await db.execute(
            select(ChannelMembersHistory)
            .where(ChannelMembersHistory.account_id == account_id)
            .where(ChannelMembersHistory.channel_id == channel_id)
            .order_by(ChannelMembersHistory.recorded_at)
        )
        hist_records = hist_q.scalars().all()

        if not tg_stats_ok and hist_records:
            start = _period_start(period)
            prev_count = None
            for rec in hist_records:
                dt = rec.recorded_at
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt.astimezone(_KYIV)
                if start and dt < start:
                    prev_count = rec.members_count
                    continue
                label = _ts_label(int(dt.timestamp() * 1000), period)
                growth_chart.append({'label': label, 'members': rec.members_count})
                if prev_count is not None:
                    diff = rec.members_count - prev_count
                    joined = max(diff, 0)
                    left = max(-diff, 0)
                    followers_chart.append({'label': label, 'joined': joined, 'left': left, 'net': diff})
                prev_count = rec.members_count

        # ── Compute summary numbers ───────────────────────────────────────
        period_growth = 0
        period_joined = sum(r['joined'] for r in followers_chart)
        period_left = sum(r['left'] for r in followers_chart)

        if growth_chart and len(growth_chart) >= 2:
            period_growth = growth_chart[-1]['members'] - growth_chart[0]['members']
        elif growth_chart and current_members:
            period_growth = current_members - growth_chart[0]['members']

        base = (current_members or 0) - period_growth
        growth_pct = round(period_growth / base * 100, 2) if base > 0 else 0

        return {
            'current_members': current_members,
            'period_growth': period_growth,
            'growth_pct': growth_pct,
            'period_joined': period_joined,
            'period_left': period_left,
            'tg_stats_ok': tg_stats_ok,
            'tg_error': tg_error,
            'growth_chart': growth_chart,
            'followers_chart': followers_chart,
            'sources': sources,
            'total_history_points': len(hist_records),
        }
    except Exception as e:
        raise HTTPException(400, f"Помилка: {str(e)[:100]}")
