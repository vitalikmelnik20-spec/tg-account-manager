import html
from datetime import datetime, timezone, timedelta, date

from sqlalchemy import select

from backend.tg_manager import tg_manager
from backend.database import AsyncSessionLocal
from backend.models import ChannelMembersHistory

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

# Track what was already sent this run (resets on restart — acceptable)
_sent_daily: set[tuple] = set()
_sent_weekly: set[tuple] = set()
_sent_monthly: set[tuple] = set()


# ── Utilities ─────────────────────────────────────────────────────────────────

def _esc(text) -> str:
    return html.escape(str(text))


def _fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}М"
    if n >= 1_000:
        return f"{n/1_000:.1f}К"
    return str(n)


def _er(reactions: int, views: int) -> float:
    if not views:
        return 0.0
    return round(reactions / views * 100, 1)


def _trend_arrow(value: int | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    arrow = "▲" if value > 0 else ("▼" if value < 0 else "➡️")
    return f"{arrow} {sign}{value}"


def _growth_pct_str(growth: int | None, current: int | None) -> str:
    if growth is None or not current or current <= abs(growth):
        return ""
    base = current - growth
    if base <= 0:
        return ""
    pct = round(growth / base * 100, 1)
    sign = "+" if pct >= 0 else ""
    return f" ({sign}{pct}%)"


# ── Data fetching ─────────────────────────────────────────────────────────────

async def _get_period_posts(client, entity, start_utc: datetime, end_utc: datetime) -> list[dict]:
    """Fetch messages in [start_utc, end_utc). Messages come newest→oldest."""
    posts = []
    async for msg in client.iter_messages(entity, limit=None):
        if not msg.date:
            continue
        msg_date = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
        if msg_date >= end_utc:
            continue  # Too new — skip, keep iterating (more old messages follow)
        if msg_date < start_utc:
            break     # Past the window — stop
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
    """Compute subscriber growth within period from DB snapshots."""
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


# ── Marketing analysis ────────────────────────────────────────────────────────

def _best_hour(posts: list[dict]) -> int | None:
    if not posts:
        return None
    hour_views: dict[int, int] = {}
    for p in posts:
        h = p['date_kyiv'].hour
        hour_views[h] = hour_views.get(h, 0) + p['views']
    return max(hour_views, key=hour_views.get)


def _media_lift(posts: list[dict]) -> float | None:
    """Returns % lift of media posts over text posts in avg views. None if can't compute."""
    media = [p for p in posts if p['has_media']]
    text = [p for p in posts if not p['has_media']]
    if not media or not text:
        return None
    avg_m = sum(p['views'] for p in media) / len(media)
    avg_t = sum(p['views'] for p in text) / len(text)
    if avg_t == 0:
        return None
    return round((avg_m - avg_t) / avg_t * 100)


def _daily_tips(posts: list[dict], growth: int | None, er: float) -> list[str]:
    tips = []
    if not posts:
        tips.append("📝 Вчора не було публікацій — постій сьогодні, аудиторія чекає!")
        return tips

    if er < 1.0:
        tips.append(f"🎯 ER {er}% — нижче норми. Спробуй запитання до аудиторії або опитування")
    elif er >= 3.0:
        tips.append(f"🔥 ER {er}% — аудиторія дуже активна! Публікуй частіше для максимального охоплення")
    else:
        tips.append(f"✅ ER {er}% — хороший показник. Продовжуй у тому ж дусі")

    h = _best_hour(posts)
    if h is not None:
        tips.append(f"⏰ Пік активності вчора: {h:02d}:00 — плануй пости на цей час")

    lift = _media_lift(posts)
    if lift is not None and lift > 15:
        tips.append(f"📸 Медіа-контент дає +{lift}% до охоплення — додавай більше фото/відео")

    if growth is not None and growth < -3:
        tips.append("⚠️ Більше відписок ніж підписок — перевір релевантність вчорашнього контенту")

    return tips[:3]


def _weekly_tips(posts: list[dict], growth: int | None, er: float) -> list[str]:
    tips = []

    if er < 1.0:
        tips.append("🎯 ER нижче 1% — протестуй нові формати: відео, опитування, інфографіка")
    elif er >= 3.0:
        tips.append("🚀 Відмінний ER! Час масштабуватись — проси підписників ділитися каналом")
    else:
        tips.append("📹 Спробуй відео-формат — він зазвичай дає на 30–50% більше охоплення")

    day_views: dict[int, dict] = {}
    for p in posts:
        d = p['date_kyiv'].weekday()
        if d not in day_views:
            day_views[d] = {'views': 0, 'cnt': 0}
        day_views[d]['views'] += p['views']
        day_views[d]['cnt'] += 1

    if day_views:
        day_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Нд']
        best_d = max(day_views, key=lambda d: day_views[d]['views'] / max(day_views[d]['cnt'], 1))
        tips.append(f"📅 Найбільше охоплення в {day_names[best_d]} — зосередь кращі пости на цей день")

    if growth is not None and growth < 0:
        tips.append("⚠️ Відплив підписників — перегляд контент-план, можливо тема не резонує")
    elif growth is not None and growth > 15:
        tips.append("📈 Сильне зростання! Закріп успішні теми та масштабуй через кросспости")

    lift = _media_lift(posts)
    if lift is not None and lift > 20:
        tips.append(f"📸 Медіа-контент дає +{lift}% охоплення — роби упор на візуал")

    return tips[:3]


def _monthly_tips(posts: list[dict], growth: int | None, er: float, current_members: int | None) -> list[str]:
    tips = []
    posts_per_day = round(len(posts) / 30, 1)

    if posts_per_day < 0.5:
        tips.append("📝 Менше 1 посту на 2 дні — збільш частоту для стабільного зростання")
    elif posts_per_day > 3:
        tips.append(f"📊 {posts_per_day} постів/день — висока частота. Переконайся, що якість не страждає")

    if er < 1.0:
        tips.append("🎯 ER нижче 1% — протестуй різні формати та теми для підвищення залученості")
    elif er >= 3.0:
        tips.append("🏆 ER вище 3% — канал дуже залучений. Відмінний час для монетизації або реклами")

    if growth is not None and current_members and growth > 0:
        daily = round(growth / 30, 1)
        tips.append(f"📈 Темп зростання: +{daily} підписників/день. Для прискорення — колаборації або реклама")
    elif growth is not None and growth < -5:
        tips.append("🔴 Значний відплив за місяць — проведи опитування аудиторії щодо контенту")

    if len(posts) >= 5:
        top = max(posts, key=lambda p: p['views'])
        avg = sum(p['views'] for p in posts) / len(posts)
        if top['views'] > avg * 2:
            preview = _esc(top['text'][:50].replace('\n', ' ')) or '[медіа]'
            tips.append(f"⭐ Найкращий тип контенту: постів схожих на «{preview}» — роби більше таких")

    return tips[:3]


# ── Report builders ───────────────────────────────────────────────────────────

def _build_daily_report(
    channel_title: str,
    channel_username: str | None,
    posts: list[dict],
    members_count: int | None,
    growth: int | None,
    report_date: date,
) -> str:
    date_str = f"{report_date.day} {_UA_MONTHS[report_date.month]} {report_date.year}"
    title = _esc(channel_title)

    total_views = sum(p['views'] for p in posts)
    total_reactions = sum(p['reactions'] for p in posts)
    total_forwards = sum(p['forwards'] for p in posts)
    avg_views = total_views // len(posts) if posts else 0
    er = _er(total_reactions, total_views)
    best_post = max(posts, key=lambda p: p['views']) if posts else None

    lines = [
        f"<b>📊 Щоденний звіт | {title}</b>",
        f"<i>📅 {date_str}</i>",
        "",
        "<b>👥 Підписники</b>",
    ]
    if members_count:
        lines.append(f"• Загалом: <b>{_fmt(members_count)}</b>")
    lines.append(f"• За день: <b>{_trend_arrow(growth)}</b>")

    lines += [
        "",
        "<b>👁 Контент вчора</b>",
        f"• Постів: <b>{len(posts)}</b>",
        f"• Переглядів: <b>{_fmt(total_views)}</b>",
        f"• Середній перегляд: <b>{_fmt(avg_views)}</b>",
        f"• Реакції: <b>{_fmt(total_reactions)}</b>  •  ER: <b>{er}%</b>",
        f"• Репости: <b>{_fmt(total_forwards)}</b>",
    ]

    if best_post:
        preview = _esc(best_post['text'][:80].replace('\n', ' ')) or '<i>[медіа без тексту]</i>'
        lines += [
            "",
            "<b>🏆 Кращий пост вчора</b>",
            f"<i>«{preview}»</i>",
            f"👁 {_fmt(best_post['views'])}  •  ❤️ {best_post['reactions']}  •  🔄 {best_post['forwards']}",
        ]
        if channel_username:
            lines.append(f"<a href=\"https://t.me/{channel_username}/{best_post['id']}\">Відкрити пост →</a>")

    tips = _daily_tips(posts, growth, er)
    if tips:
        lines += ["", "<b>💡 Рекомендації</b>"] + tips

    lines += [""]
    if growth is None:
        lines.append("<b>➡️ Оцінка дня:</b> Немає даних по підписникам")
    elif growth > 0:
        lines.append("<b>📈 Оцінка дня:</b> Канал росте — гарна динаміка! Продовжуй у тому ж напрямку ✅")
    elif growth < 0:
        lines.append("<b>📉 Оцінка дня:</b> Є відписки — варто переглянути вчорашній контент ⚠️")
    else:
        lines.append("<b>➡️ Оцінка дня:</b> Аудиторія стабільна — є потенціал для зростання")

    return "\n".join(lines)


def _build_weekly_report(
    channel_title: str,
    channel_username: str | None,
    posts: list[dict],
    members_count: int | None,
    growth: int | None,
    week_start: date,
) -> str:
    week_end = week_start + timedelta(days=6)
    start_str = f"{week_start.day} {_UA_MONTHS[week_start.month]}"
    end_str = f"{week_end.day} {_UA_MONTHS[week_end.month]} {week_end.year}"
    title = _esc(channel_title)

    total_views = sum(p['views'] for p in posts)
    total_reactions = sum(p['reactions'] for p in posts)
    total_forwards = sum(p['forwards'] for p in posts)
    avg_views = total_views // len(posts) if posts else 0
    er = _er(total_reactions, total_views)
    top_posts = sorted(posts, key=lambda p: p['views'], reverse=True)[:3]
    gp_str = _growth_pct_str(growth, members_count)

    lines = [
        f"<b>📊 Тижневий звіт | {title}</b>",
        f"<i>📅 {start_str} – {end_str}</i>",
        "",
        "<b>👥 Підписники</b>",
    ]
    if members_count:
        lines.append(f"• Загалом: <b>{_fmt(members_count)}</b>")
    lines.append(f"• За тиждень: <b>{_trend_arrow(growth)}{gp_str}</b>")

    lines += [
        "",
        "<b>📝 Контент за тиждень</b>",
        f"• Постів: <b>{len(posts)}</b>  (~{round(len(posts)/7, 1)}/день)",
        f"• Переглядів: <b>{_fmt(total_views)}</b>",
        f"• Середній перегляд: <b>{_fmt(avg_views)}</b>",
        f"• ER: <b>{er}%</b>",
        f"• Репости: <b>{_fmt(total_forwards)}</b>",
    ]

    if top_posts:
        medals = ["🥇", "🥈", "🥉"]
        lines += ["", "<b>🏆 Топ пости тижня</b>"]
        for i, p in enumerate(top_posts):
            preview = _esc(p['text'][:60].replace('\n', ' ')) or '[медіа]'
            link = f" <a href=\"https://t.me/{channel_username}/{p['id']}\">→</a>" if channel_username else ""
            lines.append(f"{medals[i]} {_fmt(p['views'])} 👁  |  «{preview}»{link}")

    tips = _weekly_tips(posts, growth, er)
    if tips:
        lines += ["", "<b>🎯 Стратегія на наступний тиждень</b>"] + tips

    lines += [""]
    if growth is not None and growth > 0 and er >= 1.5:
        lines.append("<b>✅ Підсумок тижня:</b> Канал зростає з хорошою залученістю. Чудово!")
    elif growth is not None and growth < 0:
        lines.append("<b>⚠️ Підсумок тижня:</b> Канал втрачає підписників. Потрібна корекція контент-стратегії.")
    else:
        lines.append("<b>➡️ Підсумок тижня:</b> Стабільні результати. Є куди рости — пробуй нові формати.")

    return "\n".join(lines)


def _build_monthly_report(
    channel_title: str,
    channel_username: str | None,
    posts: list[dict],
    members_count: int | None,
    growth: int | None,
    month_start: date,
) -> str:
    month_str = f"{_UA_MONTHS_NOM[month_start.month]} {month_start.year}"
    title = _esc(channel_title)

    total_views = sum(p['views'] for p in posts)
    total_reactions = sum(p['reactions'] for p in posts)
    total_forwards = sum(p['forwards'] for p in posts)
    avg_views = total_views // len(posts) if posts else 0
    er = _er(total_reactions, total_views)
    top_posts = sorted(posts, key=lambda p: p['views'], reverse=True)[:5]
    posts_per_week = round(len(posts) / 4.3, 1)
    gp_str = _growth_pct_str(growth, members_count)

    lines = [
        f"<b>📊 Місячний звіт | {title}</b>",
        f"<i>📅 {month_str}</i>",
        "",
        "<b>👥 Підписники</b>",
    ]
    if members_count:
        lines.append(f"• Загалом: <b>{_fmt(members_count)}</b>")
    lines.append(f"• За місяць: <b>{_trend_arrow(growth)}{gp_str}</b>")

    lines += [
        "",
        "<b>📝 Контент за місяць</b>",
        f"• Постів: <b>{len(posts)}</b>  (~{posts_per_week}/тиж)",
        f"• Переглядів: <b>{_fmt(total_views)}</b>",
        f"• Середній перегляд: <b>{_fmt(avg_views)}</b>",
        f"• ER: <b>{er}%</b>",
        f"• Репости: <b>{_fmt(total_forwards)}</b>",
    ]

    if top_posts:
        lines += ["", "<b>🏆 Топ-5 постів місяця</b>"]
        for i, p in enumerate(top_posts, 1):
            preview = _esc(p['text'][:55].replace('\n', ' ')) or '[медіа]'
            link = f" <a href=\"https://t.me/{channel_username}/{p['id']}\">→</a>" if channel_username else ""
            lines.append(f"{i}. {_fmt(p['views'])} 👁  |  «{preview}»{link}")

    tips = _monthly_tips(posts, growth, er, members_count)
    if tips:
        lines += ["", "<b>🎯 Маркетинг на наступний місяць</b>"] + tips

    # Verdict score
    score = sum([
        (growth or 0) > 0,
        er >= 1.5,
        avg_views > 300,
    ])
    lines += [""]
    if score == 3:
        lines.append("<b>🌟 Підсумок місяця:</b> Канал у відмінній формі — ростеш по всіх метриках! 🚀")
    elif score == 2:
        lines.append("<b>✅ Підсумок місяця:</b> Добрі результати. Є одна–дві точки для покращення.")
    elif score == 1:
        lines.append("<b>⚠️ Підсумок місяця:</b> Нижче потенціалу. Зосередься на ключових метриках.")
    else:
        lines.append("<b>🔴 Підсумок місяця:</b> Канал потребує серйозного перегляду стратегії.")

    return "\n".join(lines)


# ── Sending logic ─────────────────────────────────────────────────────────────

async def send_reports(report_type: str):
    """Generate and send Telegram reports to Saved Messages for all admin channels."""
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

                # Deduplication keys
                day_key = (account_id, channel_id, today.isoformat())
                iso = today.isocalendar()
                week_key = (account_id, channel_id, f"{iso[0]}-W{iso[1]}")
                month_key = (account_id, channel_id, f"{today.year}-{today.month}")

                if report_type == 'day' and day_key in _sent_daily:
                    continue
                if report_type == 'week' and week_key in _sent_weekly:
                    continue
                if report_type == 'month' and month_key in _sent_monthly:
                    continue

                try:
                    ch_entity = await client.get_entity(PeerChannel(channel_id))

                    # Determine time window
                    if report_type == 'day':
                        end_kyiv = now_kyiv.replace(hour=0, minute=0, second=0, microsecond=0)
                        start_kyiv = end_kyiv - timedelta(days=1)
                        report_date = today - timedelta(days=1)
                    elif report_type == 'week':
                        # Last Mon-Sun
                        days_back = today.weekday() + 7
                        last_mon = today - timedelta(days=days_back)
                        start_kyiv = datetime.combine(last_mon, datetime.min.time()).replace(tzinfo=_KYIV)
                        end_kyiv = start_kyiv + timedelta(days=7)
                        report_date = last_mon
                    else:  # month
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

                    if report_type == 'day':
                        text = _build_daily_report(channel_title, channel_username, posts, members_count, growth, report_date)
                        _sent_daily.add(day_key)
                    elif report_type == 'week':
                        text = _build_weekly_report(channel_title, channel_username, posts, members_count, growth, report_date)
                        _sent_weekly.add(week_key)
                    else:
                        text = _build_monthly_report(channel_title, channel_username, posts, members_count, growth, report_date)
                        _sent_monthly.add(month_key)

                    await client.send_message('me', text, parse_mode='html', link_preview=False)
                    print(f"[report] {report_type} → {channel_title} (acc {account_id})")

                except Exception as e:
                    print(f"[report] channel {channel_id} error: {e}")

        except Exception as e:
            print(f"[report] account {account_id} error: {e}")
