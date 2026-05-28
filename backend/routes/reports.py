import json as _json
from datetime import datetime, timezone, timedelta, date

from sqlalchemy import select

from backend.tg_manager import tg_manager
from backend.database import AsyncSessionLocal
from backend.models import ChannelMembersHistory, Notification, NotifChannelDisabled

_KYIV = timezone(timedelta(hours=3))

_UA_MONTHS = {
    1: 'Січня', 2: 'Лютого', 3: 'Березня', 4: 'Квітня',
    5: 'Травня', 6: 'Червня', 7: 'Липня', 8: 'Серпня',
    9: 'Вересня', 10: 'Жовтня', 11: 'Листопада', 12: 'Грудня',
}
_UA_MONTHS_NOM = {
    1: 'Січень', 2: 'Лютий', 3: 'Березень', 4: 'Квітень',
    5: 'Травень', 6: 'Червень', 7: 'Липень', 8: 'Серпень',
    9: 'Вересень', 10: 'Жовтень', 11: 'Листопад', 12: 'Грудень',
}
_UA_DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Нд']

_sent_daily: set[tuple] = set()
_sent_weekly: set[tuple] = set()
_sent_monthly: set[tuple] = set()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _er(reactions: int, views: int) -> float:
    return round(reactions / views * 100, 1) if views else 0.0


def _fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}М"
    if n >= 1_000:
        return f"{n / 1_000:.1f}К"
    return str(n)


def _growth_pct(growth: int | None, current: int | None) -> float | None:
    if growth is None or not current or current <= abs(growth):
        return None
    base = current - growth
    return round(growth / base * 100, 1) if base > 0 else None


def _build_compare(report_type: str, prev_notifs: list) -> dict | None:
    if not prev_notifs:
        return None
    parsed = []
    for n in prev_notifs:
        try:
            parsed.append(_json.loads(n.report_data))
        except Exception:
            pass
    if not parsed:
        return None

    prev = parsed[0]

    if report_type == 'day':
        def _avg(lst, key, dec=0):
            vals = [d[key] for d in lst if d.get(key) is not None]
            if not vals:
                return None
            return round(sum(vals) / len(vals)) if dec == 0 else round(sum(vals) / len(vals), dec)

        w = parsed[:min(7, len(parsed))]
        m = parsed[:min(30, len(parsed))]
        return {
            "prev_views": prev.get('total_views'),
            "prev_er": prev.get('er'),
            "prev_growth": prev.get('growth'),
            "week_avg_views": _avg(w, 'total_views'),
            "week_avg_er": _avg(w, 'er', dec=1),
            "month_avg_views": _avg(m, 'total_views'),
            "month_avg_er": _avg(m, 'er', dec=1),
        }
    else:
        return {
            "prev_views": prev.get('total_views'),
            "prev_er": prev.get('er'),
            "prev_growth": prev.get('growth'),
        }


# ── Data fetching ─────────────────────────────────────────────────────────────

async def _get_period_posts(client, entity, start_utc: datetime, end_utc: datetime) -> list[dict]:
    posts = []
    async for msg in client.iter_messages(entity, limit=None):
        if not msg.date:
            continue
        msg_date = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
        if msg_date >= end_utc:
            continue
        if msg_date < start_utc:
            break
        if not msg.message and not msg.media:
            continue
        react_total = 0
        if msg.reactions:
            for r in msg.reactions.results:
                react_total += r.count
        posts.append({
            'id': msg.id,
            'text': (msg.message or '')[:120],
            'views': msg.views or 0,
            'forwards': msg.forwards or 0,
            'reactions': react_total,
            'date_kyiv': msg_date.astimezone(_KYIV),
            'has_media': bool(msg.media),
        })
    return posts


async def _get_db_growth(account_id: int, channel_id: int, start_utc: datetime) -> int | None:
    async with AsyncSessionLocal() as db:
        q = await db.execute(
            select(ChannelMembersHistory)
            .where(ChannelMembersHistory.account_id == account_id)
            .where(ChannelMembersHistory.channel_id == channel_id)
            .order_by(ChannelMembersHistory.recorded_at)
        )
        records = q.scalars().all()
    before = None
    after = None
    for rec in records:
        dt = rec.recorded_at if rec.recorded_at.tzinfo else rec.recorded_at.replace(tzinfo=timezone.utc)
        if dt < start_utc:
            before = rec.members_count
        elif after is None:
            after = rec.members_count
    if before is not None and after is not None:
        return after - before
    return None


# ── Analysis ──────────────────────────────────────────────────────────────────

def _best_hour(posts: list[dict]) -> int | None:
    if not posts:
        return None
    hv: dict[int, int] = {}
    for p in posts:
        h = p['date_kyiv'].hour
        hv[h] = hv.get(h, 0) + p['views']
    return max(hv, key=hv.get)


def _media_lift(posts: list[dict]) -> int | None:
    media = [p for p in posts if p['has_media']]
    text = [p for p in posts if not p['has_media']]
    if not media or not text:
        return None
    avg_m = sum(p['views'] for p in media) / len(media)
    avg_t = sum(p['views'] for p in text) / len(text)
    if avg_t == 0:
        return None
    lift = round((avg_m - avg_t) / avg_t * 100)
    return lift if lift > 10 else None


def _daily_tips(posts, growth, er) -> list[str]:
    tips = []
    if not posts:
        tips.append("📝 Вчора не було публікацій — постій сьогодні, аудиторія чекає!")
        return tips
    if er < 1.0:
        tips.append(f"🎯 ER {er}% — нижче норми. Спробуй запитання або опитування для залучення")
    elif er >= 3.0:
        tips.append(f"🔥 ER {er}% — аудиторія дуже активна! Публікуй частіше для максимального охоплення")
    else:
        tips.append(f"✅ ER {er}% — хороший показник. Продовжуй у тому ж дусі")
    h = _best_hour(posts)
    if h is not None:
        tips.append(f"⏰ Пік активності вчора: {h:02d}:00 — плануй пости на цей час")
    lift = _media_lift(posts)
    if lift is not None:
        tips.append(f"📸 Медіа-контент дає +{lift}% до охоплення — додавай більше фото/відео")
    if growth is not None and growth < -3:
        tips.append("⚠️ Більше відписок ніж підписок — перевір релевантність вчорашнього контенту")
    return tips[:3]


def _weekly_tips(posts, growth, er) -> list[str]:
    tips = []
    if er < 1.0:
        tips.append("🎯 ER нижче 1% — протестуй нові формати: відео, опитування, інфографіка")
    elif er >= 3.0:
        tips.append("🚀 Відмінний ER! Час масштабуватись — проси підписників ділитися каналом")
    else:
        tips.append("📹 Спробуй відео-формат — він зазвичай дає на 30–50% більше охоплення")
    dv: dict[int, dict] = {}
    for p in posts:
        d = p['date_kyiv'].weekday()
        if d not in dv:
            dv[d] = {'views': 0, 'cnt': 0}
        dv[d]['views'] += p['views']
        dv[d]['cnt'] += 1
    if dv:
        best_d = max(dv, key=lambda d: dv[d]['views'] / max(dv[d]['cnt'], 1))
        tips.append(f"📅 Найбільше охоплення в {_UA_DAYS[best_d]} — зосередь кращі пости на цей день")
    if growth is not None and growth < 0:
        tips.append("⚠️ Відплив підписників — переглянь контент-план, можливо тема не резонує")
    elif growth is not None and growth > 15:
        tips.append("📈 Сильне зростання! Закріп успішні теми та масштабуй через кросспости")
    lift = _media_lift(posts)
    if lift is not None:
        tips.append(f"📸 Медіа-контент дає +{lift}% охоплення — роби упор на візуал")
    return tips[:3]


def _monthly_tips(posts, growth, er, current_members) -> list[str]:
    tips = []
    posts_per_day = len(posts) / 30 if posts else 0
    if posts_per_day < 0.5:
        tips.append("📝 Менше 1 посту на 2 дні — збільш частоту для стабільного зростання")
    elif posts_per_day > 3:
        tips.append(f"📊 {round(posts_per_day, 1)} постів/день — висока частота. Переконайся, що якість не страждає")
    if er < 1.0:
        tips.append("🎯 ER нижче 1% — протестуй різні формати та теми для підвищення залученості")
    elif er >= 3.0:
        tips.append("🏆 ER вище 3% — канал дуже залучений. Відмінний час для монетизації або реклами")
    if growth is not None and growth > 0:
        daily = round(growth / 30, 1)
        tips.append(f"📈 Темп зростання: +{daily}/день. Для прискорення — колаборації або реклама")
    elif growth is not None and growth < -5:
        tips.append("🔴 Значний відплив за місяць — проведи опитування аудиторії щодо контенту")
    if len(posts) >= 5:
        top = max(posts, key=lambda p: p['views'])
        avg = sum(p['views'] for p in posts) / len(posts)
        if top['views'] > avg * 2:
            preview = top['text'][:50].replace('\n', ' ') or '[медіа]'
            tips.append(f"⭐ Найкращий тип контенту — схожих постів треба більше")
    return tips[:3]


# ── Report data builders ──────────────────────────────────────────────────────

def _verdict(growth, er, avg_views=0) -> tuple[str, str]:
    if growth is None:
        return "neutral", "Немає даних по підписникам"
    if growth > 0 and er >= 1.5:
        return "positive", "Канал росте з хорошою залученістю — відмінна динаміка! ✅"
    if growth > 0:
        return "positive", "Канал росте — гарна динаміка! Продовжуй у тому ж напрямку ✅"
    if growth < 0:
        return "negative", "Є відписки — варто переглянути контент-стратегію ⚠️"
    return "neutral", "Аудиторія стабільна — є потенціал для зростання"


def _build_daily_data(channel_title, channel_username, posts, members_count, growth, report_date: date, compare=None) -> dict:
    total_views = sum(p['views'] for p in posts)
    total_reactions = sum(p['reactions'] for p in posts)
    total_forwards = sum(p['forwards'] for p in posts)
    avg_views = total_views // len(posts) if posts else 0
    er = _er(total_reactions, total_views)
    best_post = max(posts, key=lambda p: p['views']) if posts else None
    vrd, vrd_text = _verdict(growth, er, avg_views)
    return {
        "period_str": f"{report_date.day} {_UA_MONTHS[report_date.month]} {report_date.year}",
        "channel_username": channel_username,
        "members_count": members_count,
        "growth": growth,
        "growth_pct": _growth_pct(growth, members_count),
        "total_posts": len(posts),
        "total_views": total_views,
        "avg_views": avg_views,
        "total_reactions": total_reactions,
        "total_forwards": total_forwards,
        "er": er,
        "best_post": {
            "id": best_post['id'],
            "text": best_post['text'][:100],
            "views": best_post['views'],
            "reactions": best_post['reactions'],
            "forwards": best_post['forwards'],
        } if best_post else None,
        "top_posts": None,
        "tips": _daily_tips(posts, growth, er),
        "verdict": vrd,
        "verdict_text": vrd_text,
        "compare": compare,
    }


def _build_weekly_data(channel_title, channel_username, posts, members_count, growth, week_start: date, compare=None) -> dict:
    week_end = week_start + timedelta(days=6)
    s = f"{week_start.day} {_UA_MONTHS[week_start.month]}"
    e = f"{week_end.day} {_UA_MONTHS[week_end.month]} {week_end.year}"
    total_views = sum(p['views'] for p in posts)
    total_reactions = sum(p['reactions'] for p in posts)
    total_forwards = sum(p['forwards'] for p in posts)
    avg_views = total_views // len(posts) if posts else 0
    er = _er(total_reactions, total_views)
    top_posts = [
        {"id": p['id'], "text": p['text'][:80], "views": p['views'],
         "reactions": p['reactions'], "forwards": p['forwards']}
        for p in sorted(posts, key=lambda p: p['views'], reverse=True)[:3]
    ]
    # Best day
    dv: dict[int, dict] = {}
    for p in posts:
        d = p['date_kyiv'].weekday()
        if d not in dv:
            dv[d] = {'views': 0, 'cnt': 0}
        dv[d]['views'] += p['views']
        dv[d]['cnt'] += 1
    best_day_name = None
    if dv:
        bd = max(dv, key=lambda d: dv[d]['views'] / max(dv[d]['cnt'], 1))
        best_day_name = _UA_DAYS[bd]
    vrd, vrd_text = _verdict(growth, er, avg_views)
    return {
        "period_str": f"{s} – {e}",
        "channel_username": channel_username,
        "members_count": members_count,
        "growth": growth,
        "growth_pct": _growth_pct(growth, members_count),
        "total_posts": len(posts),
        "total_views": total_views,
        "avg_views": avg_views,
        "total_reactions": total_reactions,
        "total_forwards": total_forwards,
        "er": er,
        "best_post": None,
        "top_posts": top_posts,
        "best_day_name": best_day_name,
        "posts_per_day": round(len(posts) / 7, 1),
        "tips": _weekly_tips(posts, growth, er),
        "verdict": vrd,
        "verdict_text": vrd_text,
        "compare": compare,
    }


def _build_monthly_data(channel_title, channel_username, posts, members_count, growth, month_start: date, compare=None) -> dict:
    total_views = sum(p['views'] for p in posts)
    total_reactions = sum(p['reactions'] for p in posts)
    total_forwards = sum(p['forwards'] for p in posts)
    avg_views = total_views // len(posts) if posts else 0
    er = _er(total_reactions, total_views)
    top_posts = [
        {"id": p['id'], "text": p['text'][:80], "views": p['views'],
         "reactions": p['reactions'], "forwards": p['forwards']}
        for p in sorted(posts, key=lambda p: p['views'], reverse=True)[:5]
    ]
    score = sum([(growth or 0) > 0, er >= 1.5, avg_views > 300])
    if score == 3:
        vrd, vrd_text = "positive", "Канал у відмінній формі — ростеш по всіх метриках! 🚀"
    elif score == 2:
        vrd, vrd_text = "positive", "Добрі результати. Є одна–дві точки для покращення ✅"
    elif score == 1:
        vrd, vrd_text = "neutral", "Нижче потенціалу. Зосередься на ключових метриках ⚠️"
    else:
        vrd, vrd_text = "negative", "Канал потребує серйозного перегляду стратегії 🔴"
    return {
        "period_str": f"{_UA_MONTHS_NOM[month_start.month]} {month_start.year}",
        "channel_username": channel_username,
        "members_count": members_count,
        "growth": growth,
        "growth_pct": _growth_pct(growth, members_count),
        "total_posts": len(posts),
        "total_views": total_views,
        "avg_views": avg_views,
        "total_reactions": total_reactions,
        "total_forwards": total_forwards,
        "er": er,
        "best_post": None,
        "top_posts": top_posts,
        "posts_per_week": round(len(posts) / 4.3, 1),
        "tips": _monthly_tips(posts, growth, er, members_count),
        "verdict": vrd,
        "verdict_text": vrd_text,
        "compare": compare,
    }


# ── Main entry point ──────────────────────────────────────────────────────────

async def generate_reports(report_type: str):
    """Generate reports for all admin channels and save as notifications to DB."""
    from telethon.tl.types import Channel, PeerChannel

    now_kyiv = datetime.now(_KYIV)
    today = now_kyiv.date()

    for account_id, client in list(tg_manager.clients.items()):
        try:
            async for dialog in client.iter_dialogs(limit=500):
                entity = dialog.entity
                if not isinstance(entity, Channel) or not entity.broadcast or entity.left:
                    continue
                if not entity.creator and not entity.admin_rights:
                    continue

                channel_id = entity.id
                channel_title = entity.title
                channel_username = entity.username
                members_count = getattr(entity, 'participants_count', None)

                # Dedup keys per channel only (no account_id — multiple admins = 1 notification)
                iso = today.isocalendar()
                day_key = (channel_id, today.isoformat())
                week_key = (channel_id, f"{iso[0]}-W{iso[1]}")
                month_key = (channel_id, f"{today.year}-{today.month}")

                if report_type == 'day' and day_key in _sent_daily:
                    continue
                if report_type == 'week' and week_key in _sent_weekly:
                    continue
                if report_type == 'month' and month_key in _sent_monthly:
                    continue

                # DB-level dedup: survive server restarts
                async with AsyncSessionLocal() as db:
                    period_start = {
                        'day': datetime.combine(today, datetime.min.time()).replace(tzinfo=_KYIV),
                        'week': datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time()).replace(tzinfo=_KYIV),
                        'month': datetime.combine(today.replace(day=1), datetime.min.time()).replace(tzinfo=_KYIV),
                    }[report_type]
                    already = await db.execute(
                        select(Notification.id)
                        .where(Notification.channel_id == channel_id)
                        .where(Notification.report_type == report_type)
                        .where(Notification.created_at >= period_start)
                        .limit(1)
                    )
                    if already.scalar():
                        # Mark in-memory too so we skip on next iteration without hitting DB
                        if report_type == 'day': _sent_daily.add(day_key)
                        elif report_type == 'week': _sent_weekly.add(week_key)
                        else: _sent_monthly.add(month_key)
                        continue

                # Check if channel is disabled in filter
                async with AsyncSessionLocal() as db:
                    if await db.get(NotifChannelDisabled, channel_id):
                        continue

                try:
                    ch_entity = await client.get_entity(PeerChannel(channel_id))

                    if report_type == 'day':
                        end_kyiv = now_kyiv.replace(hour=0, minute=0, second=0, microsecond=0)
                        start_kyiv = end_kyiv - timedelta(days=1)
                        report_date = today - timedelta(days=1)
                    elif report_type == 'week':
                        days_back = today.weekday() + 7
                        last_mon = today - timedelta(days=days_back)
                        start_kyiv = datetime.combine(last_mon, datetime.min.time()).replace(tzinfo=_KYIV)
                        end_kyiv = start_kyiv + timedelta(days=7)
                        report_date = last_mon
                    else:
                        first_this = today.replace(day=1)
                        last_month_end = first_this - timedelta(days=1)
                        last_month_start = last_month_end.replace(day=1)
                        start_kyiv = datetime.combine(last_month_start, datetime.min.time()).replace(tzinfo=_KYIV)
                        end_kyiv = datetime.combine(first_this, datetime.min.time()).replace(tzinfo=_KYIV)
                        report_date = last_month_start

                    start_utc = start_kyiv.astimezone(timezone.utc)
                    end_utc = end_kyiv.astimezone(timezone.utc)

                    posts = await _get_period_posts(client, ch_entity, start_utc, end_utc)
                    growth = await _get_db_growth(account_id, channel_id, start_utc)

                    # Fetch previous notifications for comparison
                    async with AsyncSessionLocal() as db:
                        q_prev = await db.execute(
                            select(Notification)
                            .where(Notification.channel_id == channel_id)
                            .where(Notification.report_type == report_type)
                            .order_by(Notification.created_at.desc())
                            .limit(30)
                        )
                        prev_notifs = q_prev.scalars().all()
                    compare = _build_compare(report_type, prev_notifs)

                    if report_type == 'day':
                        data = _build_daily_data(channel_title, channel_username, posts, members_count, growth, report_date, compare=compare)
                        _sent_daily.add(day_key)
                    elif report_type == 'week':
                        data = _build_weekly_data(channel_title, channel_username, posts, members_count, growth, report_date, compare=compare)
                        _sent_weekly.add(week_key)
                    else:
                        data = _build_monthly_data(channel_title, channel_username, posts, members_count, growth, report_date, compare=compare)
                        _sent_monthly.add(month_key)

                    async with AsyncSessionLocal() as db:
                        db.add(Notification(
                            report_type=report_type,
                            channel_id=channel_id,
                            channel_title=channel_title,
                            report_data=_json.dumps(data, ensure_ascii=False),
                        ))
                        await db.commit()

                    print(f"[report] {report_type} saved for {channel_title} (acc {account_id})")

                except Exception as e:
                    print(f"[report] channel {channel_id}: {e}")

        except Exception as e:
            print(f"[report] account {account_id}: {e}")
