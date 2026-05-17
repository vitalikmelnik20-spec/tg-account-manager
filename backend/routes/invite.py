import asyncio
import csv
import io
import json
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, Form
from typing import List, Optional

from backend.tg_manager import tg_manager

router = APIRouter(prefix="/api/invite")

_state = {
    "running": False,
    "task": None,
    "total": 0,
    "done": 0,
    "success": 0,
    "failed": 0,
    "skipped": 0,
    "log": [],
    # parse
    "parse_running": False,
    "parsed_users": [],
    "parse_count": 0,
}


def _ts():
    return datetime.now().strftime("%H:%M:%S")


async def _broadcast(entry: dict):
    from backend.routes.ws import manager as ws_manager
    _state["log"].append(entry)
    if len(_state["log"]) > 5000:
        _state["log"] = _state["log"][-4000:]
    await ws_manager.broadcast({"type": "invite_log", **entry})


async def _push_stats():
    from backend.routes.ws import manager as ws_manager
    await ws_manager.broadcast({
        "type": "invite_stats",
        "total": _state["total"],
        "done": _state["done"],
        "success": _state["success"],
        "failed": _state["failed"],
        "skipped": _state["skipped"],
        "running": _state["running"],
    })


def _parse_csv(content: bytes) -> List[str]:
    text = content.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []

    headers = [h.lower().strip() for h in rows[0]]
    user_col = None
    for i, h in enumerate(headers):
        if h in ("username", "user", "handle", "login"):
            user_col = i
            break
    if user_col is None:
        for i, h in enumerate(headers):
            if h in ("id", "user_id", "telegram_id", "tg_id"):
                user_col = i
                break

    if user_col is not None:
        users = []
        for row in rows[1:]:
            if len(row) > user_col:
                val = row[user_col].strip().lstrip("@")
                if val:
                    users.append(val)
        return users
    else:
        users = []
        for row in rows:
            if row and row[0].strip():
                val = row[0].strip().lstrip("@")
                if val:
                    users.append(val)
        return users


async def _run_invite(users: List[str], account_ids: List[int], channel: str, interval: float):
    from telethon.errors import (
        UserPrivacyRestrictedError, UserNotMutualContactError,
        UserAlreadyParticipantError, FloodWaitError, PeerFloodError,
        InputUserDeactivatedError, UserBannedInChannelError,
        ChatAdminRequiredError,
    )
    from telethon.tl.functions.channels import InviteToChannelRequest

    _state["total"] = len(users)
    _state["done"] = 0
    _state["success"] = 0
    _state["failed"] = 0
    _state["skipped"] = 0

    flood_until = {}
    channel_entities = {}

    # Resolve display name for each account once at startup
    acc_names = {}
    for aid in account_ids:
        client = tg_manager.clients.get(aid)
        if client:
            try:
                me = await client.get_me()
                acc_names[aid] = me.first_name or f"@{me.username}" if me.username else f"#{aid}"
                if me.username:
                    acc_names[aid] = f"{acc_names[aid]} (@{me.username})"
            except Exception:
                acc_names[aid] = f"#{aid}"
        else:
            acc_names[aid] = f"#{aid}"

    # pointer — index of the next account to use (strict round-robin)
    pointer = 0

    await _broadcast({"level": "info", "msg": f"[{_ts()}] ▶ СТАРТ  users={len(users)}  accounts={len(account_ids)}  interval={interval}s  channel={channel}"})
    accs_str = "  ".join(acc_names[a] for a in account_ids)
    await _broadcast({"level": "info", "msg": f"[{_ts()}] ℹ  Акаунти: {accs_str}"})
    await _push_stats()

    for user in users:
        if not _state["running"]:
            await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⛔ ЗУПИНЕНО ВРУЧНУ"})
            break

        loop_time = asyncio.get_event_loop().time()

        # Strict round-robin: scan from pointer, find first non-flooded
        chosen_aid = None
        chosen_ptr = None
        for offset in range(len(account_ids)):
            idx = (pointer + offset) % len(account_ids)
            aid = account_ids[idx]
            if flood_until.get(aid, 0) <= loop_time:
                chosen_aid = aid
                chosen_ptr = (idx + 1) % len(account_ids)  # next user starts after this
                break

        if chosen_aid is None:
            # All accounts flooded — wait for the earliest to clear
            min_until = min(flood_until.get(a, 0) for a in account_ids)
            wait = max(min_until - loop_time, 1)
            await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⏳ ВСІ АКАУНТИ У FLOOD  чекаємо {wait:.0f}с"})
            await asyncio.sleep(wait)
            loop_time = asyncio.get_event_loop().time()
            for offset in range(len(account_ids)):
                idx = (pointer + offset) % len(account_ids)
                aid = account_ids[idx]
                if flood_until.get(aid, 0) <= loop_time:
                    chosen_aid = aid
                    chosen_ptr = (idx + 1) % len(account_ids)
                    break

        if chosen_aid is None:
            continue  # still all flooded, skip this user

        pointer = chosen_ptr
        acc_label = acc_names.get(chosen_aid, f"#{chosen_aid}")

        client = tg_manager.clients.get(chosen_aid)
        if not client:
            _state["failed"] += 1
            _state["done"] += 1
            await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  @{user}  [{acc_label}] не підключено"})
            await _push_stats()
            continue

        # Resolve channel once per account (cached)
        if chosen_aid not in channel_entities:
            try:
                channel_entities[chosen_aid] = await client.get_entity(channel)
                await _broadcast({"level": "info", "msg": f"[{_ts()}] ℹ  [{acc_label}]  канал '{channel}' знайдено"})
            except Exception as e:
                channel_entities[chosen_aid] = None
                await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{acc_label}]  канал '{channel}' не знайдено: {e}"})

        ch_entity = channel_entities.get(chosen_aid)
        if ch_entity is None:
            _state["failed"] += 1
            _state["done"] += 1
            await _push_stats()
            if _state["done"] < _state["total"] and _state["running"]:
                await asyncio.sleep(interval)
            continue

        try:
            user_id = "@" + user if not user.startswith("@") and not user.lstrip("+").isdigit() else user
            user_entity = await client.get_entity(user_id)
            await client(InviteToChannelRequest(channel=ch_entity, users=[user_entity]))
            _state["success"] += 1
            _state["done"] += 1
            await _broadcast({"level": "ok", "msg": f"[{_ts()}] ✓  @{user}  ←  [{acc_label}]  ЗАПРОШЕНО"})

        except UserAlreadyParticipantError:
            _state["success"] += 1
            _state["done"] += 1
            await _broadcast({"level": "ok", "msg": f"[{_ts()}] ✓  @{user}  вже є учасником"})

        except (UserPrivacyRestrictedError, UserNotMutualContactError):
            _state["skipped"] += 1
            _state["done"] += 1
            await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⚠  @{user}  приватність або не взаємний контакт"})

        except InputUserDeactivatedError:
            _state["skipped"] += 1
            _state["done"] += 1
            await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⚠  @{user}  акаунт видалено/деактивовано"})

        except UserBannedInChannelError:
            _state["skipped"] += 1
            _state["done"] += 1
            await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⚠  @{user}  заблоковано в каналі"})

        except FloodWaitError as e:
            flood_until[chosen_aid] = asyncio.get_event_loop().time() + e.seconds
            _state["skipped"] += 1
            _state["done"] += 1
            await _broadcast({"level": "warn", "msg": f"[{_ts()}] ⏳  [{acc_label}]  FloodWait {e.seconds}с → заморожено"})
            await _push_stats()
            continue  # don't sleep extra, switch to next account immediately

        except PeerFloodError:
            flood_until[chosen_aid] = asyncio.get_event_loop().time() + 3600
            _state["skipped"] += 1
            _state["done"] += 1
            await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  [{acc_label}]  PeerFlood → заморожено на 1год"})
            await _push_stats()
            continue

        except ChatAdminRequiredError:
            _state["failed"] += 1
            _state["done"] += 1
            await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  @{user}  [{acc_label}]  немає прав адміна"})

        except Exception as e:
            _state["failed"] += 1
            _state["done"] += 1
            await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕  @{user}  [{acc_label}]  {str(e)[:100]}"})

        await _push_stats()

        if _state["done"] < _state["total"] and _state["running"]:
            await asyncio.sleep(interval)

    _state["running"] = False
    await _broadcast({"level": "info", "msg": f"[{_ts()}] ■ ЗАВЕРШЕНО  ✓{_state['success']}  ✕{_state['failed']}  ⚠{_state['skipped']}  /  {_state['total']}"})
    await _push_stats()


async def _run_parse(source: str, account_id: int):
    from backend.routes.ws import manager as ws_manager

    _state["parse_running"] = True
    _state["parsed_users"] = []
    _state["parse_count"] = 0

    client = tg_manager.clients.get(account_id)
    if not client:
        await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕ Акаунт #{account_id} не підключено"})
        _state["parse_running"] = False
        return

    try:
        me = await client.get_me()
        acc_label = me.first_name or f"#{account_id}"
    except Exception:
        acc_label = f"#{account_id}"

    await _broadcast({"level": "info", "msg": f"[{_ts()}] 🔍 ПАРСИНГ СТАРТ  джерело={source}  акаунт={acc_label}"})

    users = []
    try:
        async for participant in client.iter_participants(source, aggressive=True):
            if getattr(participant, "deleted", False):
                continue
            if participant.username:
                users.append(participant.username)
            # skip users without @username — can't invite by numeric ID

            _state["parse_count"] = len(users)
            if len(users) % 200 == 0:
                await _broadcast({"level": "info", "msg": f"[{_ts()}] 🔍 Зібрано: {len(users)}..."})
                await ws_manager.broadcast({"type": "invite_parse_progress", "count": len(users)})

    except Exception as e:
        await _broadcast({"level": "err", "msg": f"[{_ts()}] ✕ Помилка парсингу: {str(e)[:120]}"})

    _state["parsed_users"] = users
    _state["parse_count"] = len(users)
    _state["parse_running"] = False
    await _broadcast({"level": "ok", "msg": f"[{_ts()}] ✓ ПАРСИНГ ЗАВЕРШЕНО  зібрано {len(users)} учасників"})
    await ws_manager.broadcast({"type": "invite_parse_done", "count": len(users)})


@router.post("/parse")
async def invite_parse(
    source: str = Form(...),
    account_id: int = Form(...),
):
    if _state["parse_running"]:
        return {"error": "Парсинг вже запущено"}
    asyncio.create_task(_run_parse(source.strip(), account_id))
    return {"ok": True}


@router.post("/start")
async def invite_start(
    channel: str = Form(...),
    account_ids: str = Form("[]"),
    interval: float = Form(60.0),
    use_parsed: str = Form("false"),
    csv_file: Optional[UploadFile] = File(None),
):
    if _state["running"]:
        return {"error": "Інвайтинг вже запущено"}

    if use_parsed.lower() == "true":
        users = list(_state["parsed_users"])
        if not users:
            return {"error": "Список зібраних учасників порожній — спочатку запусти парсинг"}
    elif csv_file:
        content = await csv_file.read()
        users = _parse_csv(content)
        if not users:
            return {"error": "CSV порожній або не вдалось розпарсити"}
    else:
        return {"error": "Вибери CSV або використай зібраних учасників"}

    ids = json.loads(account_ids) or list(tg_manager.clients.keys())
    if not ids:
        return {"error": "Немає підключених акаунтів"}

    _state["running"] = True
    _state["log"] = []

    _state["task"] = asyncio.create_task(_run_invite(users, ids, channel.strip(), interval))
    return {"ok": True, "total": len(users)}


@router.post("/stop")
async def invite_stop():
    _state["running"] = False
    task = _state.get("task")
    if task and not task.done():
        task.cancel()
    return {"ok": True}


@router.get("/status")
async def invite_status():
    return {
        "running": _state["running"],
        "total": _state["total"],
        "done": _state["done"],
        "success": _state["success"],
        "failed": _state["failed"],
        "skipped": _state["skipped"],
        "log": _state["log"][-500:],
        "parse_running": _state["parse_running"],
        "parse_count": _state["parse_count"],
    }
