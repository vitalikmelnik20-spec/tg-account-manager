import asyncio
import re
import base64
import struct
from typing import Dict, Optional, Callable
from datetime import datetime, timezone, timedelta

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.crypto import AuthKey
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    UserStatusOnline, UserStatusOffline, UserStatusRecently,
    UserStatusLastWeek, UserStatusLastMonth,
)
from telethon.errors import FloodWaitError

from backend.database import AsyncSessionLocal
from backend.models import Account, OTPCode
from sqlalchemy import select


_DC_IPS = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.92",
    5: "91.108.56.190",
}

def pyrogram_to_telethon(pyro_str: str) -> str:
    """Convert Pyrogram session string to Telethon StringSession."""
    pyro_str = pyro_str.strip()
    pad = "=" * (-len(pyro_str) % 4)
    data = base64.urlsafe_b64decode(pyro_str + pad)

    dc_id = auth_key = None
    if len(data) == 263:
        # Pyrogram v1: dc_id(1) + test(1) + auth_key(256) + user_id(4) + is_bot(1)
        dc_id, _, auth_key, _, _ = struct.unpack(">B?256sI?", data)
    elif len(data) in (267, 268):
        # Pyrogram v2: dc_id(1) + test(1) + auth_key(256) + user_id(8) + is_bot(1)
        dc_id, _, auth_key, _, _ = struct.unpack(">B?256sQ?", data[:267])
    else:
        raise ValueError(
            f"Невідомий формат Pyrogram сесії (розмір: {len(data)} байт). "
            "Підтримуються v1 (263 байти) та v2 (267 байт)."
        )

    if dc_id not in _DC_IPS:
        raise ValueError(f"Невідомий DC ID в Pyrogram сесії: {dc_id}")

    session = StringSession()
    session.set_dc(dc_id, _DC_IPS[dc_id], 443)
    session.auth_key = AuthKey(data=auth_key)
    return session.save()


def normalize_session(session_string: str) -> str:
    """Return a Telethon session string, converting from Pyrogram if needed."""
    s = session_string.strip()
    if s.startswith("1"):
        return s
    return pyrogram_to_telethon(s)


def telethon_to_pyrogram(telethon_str: str, user_id: int = 0) -> str:
    """Convert Telethon session string to Pyrogram session string."""
    s = StringSession(telethon_str)
    dc_id = s.dc_id or 2
    auth_key = s.auth_key.key if s.auth_key else b'\x00' * 256
    data = struct.pack(">B?256sQ?", dc_id, False, auth_key, user_id, False)
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


# Known (user_id, unix_timestamp) reference points for registration date estimation
_TG_ID_TS = [
    (1,           1376524800),  # Aug 2013
    (10_000_000,  1413331200),  # Oct 2014
    (100_000_000, 1451606400),  # Jan 2016
    (500_000_000, 1538352000),  # Oct 2018
    (1_000_000_000, 1582934400), # Feb 2020
    (1_500_000_000, 1609459200), # Jan 2021
    (2_000_000_000, 1623456000), # Jun 2021
    (3_000_000_000, 1646092800), # Mar 2022
    (5_000_000_000, 1672531200), # Jan 2023
    (6_000_000_000, 1690848000), # Aug 2023
    (7_000_000_000, 1706745600), # Feb 2024
    (8_000_000_000, 1720742400), # Jul 2024
    (9_000_000_000, 1735689600), # Jan 2025
]

def estimate_tg_created(user_id: int) -> str | None:
    pts = _TG_ID_TS
    if user_id <= 0:
        return None
    if user_id <= pts[0][0]:
        return datetime.fromtimestamp(pts[0][1]).strftime("%m.%Y")
    for i in range(len(pts) - 1):
        lo_id, lo_ts = pts[i]
        hi_id, hi_ts = pts[i + 1]
        if lo_id <= user_id < hi_id:
            ratio = (user_id - lo_id) / (hi_id - lo_id)
            ts = lo_ts + ratio * (hi_ts - lo_ts)
            return datetime.fromtimestamp(ts).strftime("%m.%Y")
    # Beyond last point — linear extrapolation from last two
    lo_id, lo_ts = pts[-2]
    hi_id, hi_ts = pts[-1]
    ratio = (user_id - lo_id) / (hi_id - lo_id)
    ts = lo_ts + ratio * (hi_ts - lo_ts)
    return datetime.fromtimestamp(ts).strftime("%m.%Y")


def format_status(status) -> str:
    if isinstance(status, UserStatusOnline):
        return "Онлайн"
    if isinstance(status, UserStatusOffline):
        return f"Був(а) {status.was_online.strftime('%d.%m.%Y %H:%M')}"
    if isinstance(status, UserStatusRecently):
        return "Нещодавно"
    if isinstance(status, UserStatusLastWeek):
        return "Цього тижня"
    if isinstance(status, UserStatusLastMonth):
        return "Цього місяця"
    return "Невідомо"


class TGManager:
    def __init__(self):
        self.clients: Dict[int, TelegramClient] = {}
        self.tasks: Dict[int, asyncio.Task] = {}
        self._broadcaster: Optional[Callable] = None
        # {account_id: {"until": datetime, "total_seconds": int}}
        self._flood_waits: Dict[int, dict] = {}

    def set_broadcaster(self, broadcaster: Callable):
        self._broadcaster = broadcaster

    async def start(self):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Account))
            accounts = result.scalars().all()
        for acc in accounts:
            await self.connect_account(acc.id, acc.session_string, int(acc.api_id), acc.api_hash)

    async def stop(self):
        for task in self.tasks.values():
            task.cancel()
        for client in self.clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass

    async def connect_account(
        self, account_id: int, session_string: str, api_id: int, api_hash: str
    ) -> bool:
        try:
            session_string = normalize_session(session_string)
            client = TelegramClient(StringSession(session_string), api_id, api_hash)
            await client.connect()

            if not await client.is_user_authorized():
                async with AsyncSessionLocal() as db:
                    acc = await db.get(Account, account_id)
                    if acc:
                        acc.is_connected = False
                        await db.commit()
                await client.disconnect()
                return False

            me = await client.get_me()
            try:
                full = await client(GetFullUserRequest(me.id))
                bio = full.full_user.about
            except Exception:
                bio = None

            async with AsyncSessionLocal() as db:
                acc = await db.get(Account, account_id)
                if acc:
                    acc.phone = me.phone
                    acc.username = me.username
                    acc.first_name = me.first_name
                    acc.last_name = me.last_name
                    acc.bio = bio
                    acc.is_connected = True
                    await db.commit()

            @client.on(events.NewMessage(from_users=777000))
            async def otp_handler(event):
                await self._handle_otp(account_id, event.message.text)

            self.clients[account_id] = client
            self.tasks[account_id] = asyncio.create_task(client.run_until_disconnected())
            return True

        except Exception as e:
            print(f"[TGManager] Помилка підключення акаунту {account_id}: {e}")
            return False

    async def _handle_otp(self, account_id: int, text: str):
        match = re.search(r'\b(\d{5,6})\b', text)
        code = match.group(1) if match else ""

        text_lower = text.lower()
        if any(w in text_lower for w in ("two-step", "two step", "2-step", "двухэтапн", "двоетапн", "пароль", "password")):
            code_type = "2fa"
        else:
            code_type = "login"

        async with AsyncSessionLocal() as db:
            otp = OTPCode(account_id=account_id, code=code, code_type=code_type, message_text=text)
            db.add(otp)
            await db.commit()
            await db.refresh(otp)

        if self._broadcaster:
            await self._broadcaster({
                "type": "new_code",
                "account_id": account_id,
                "code": code,
                "code_type": code_type,
                "message": text,
                "received_at": datetime.now(timezone.utc).isoformat(),
            })

    async def disconnect_account(self, account_id: int):
        if account_id in self.tasks:
            self.tasks[account_id].cancel()
            del self.tasks[account_id]
        if account_id in self.clients:
            try:
                await self.clients[account_id].disconnect()
            except Exception:
                pass
            del self.clients[account_id]

    async def get_profile_photo(self, account_id: int) -> Optional[bytes]:
        if account_id not in self.clients:
            return None
        client = self.clients[account_id]
        try:
            me = await client.get_me()
            return await client.download_profile_photo(me, bytes)
        except Exception:
            return None

    def record_flood(self, account_id: int, seconds: int):
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        self._flood_waits[account_id] = {"until": until, "total_seconds": seconds}

    async def check_status(self, account_id: int) -> dict:
        if account_id not in self.clients:
            return {"connected": False, "flood": None, "restricted": False}

        client = self.clients[account_id]

        # Якщо є збережений flood wait і він ще активний — повертаємо його
        if account_id in self._flood_waits:
            fw = self._flood_waits[account_id]
            now = datetime.now(timezone.utc)
            if now < fw["until"]:
                seconds_left = int((fw["until"] - now).total_seconds())
                return {
                    "connected": True,
                    "flood": {
                        "active": True,
                        "seconds_left": seconds_left,
                        "total_seconds": fw["total_seconds"],
                        "expires_at": fw["until"].isoformat(),
                    },
                    "restricted": False,
                }
            else:
                del self._flood_waits[account_id]

        # Робимо тестовий запит
        try:
            me = await client.get_me()
            restricted = bool(getattr(me, "restricted", False))
            reason = None
            if restricted:
                rr = getattr(me, "restriction_reason", None)
                if rr:
                    reason = rr[0].reason if hasattr(rr[0], "reason") else str(rr[0])
            return {
                "connected": True,
                "flood": {"active": False, "seconds_left": 0, "total_seconds": 0},
                "restricted": restricted,
                "restriction_reason": reason,
            }
        except FloodWaitError as e:
            self.record_flood(account_id, e.seconds)
            return {
                "connected": True,
                "flood": {
                    "active": True,
                    "seconds_left": e.seconds,
                    "total_seconds": e.seconds,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=e.seconds)).isoformat(),
                },
                "restricted": False,
            }
        except Exception as e:
            return {"connected": False, "flood": None, "restricted": False, "error": str(e)}

    async def get_full_info(self, account_id: int) -> Optional[dict]:
        if account_id not in self.clients:
            return None
        client = self.clients[account_id]
        try:
            me = await client.get_me()
            full = await client(GetFullUserRequest(me.id))
            return {
                "tg_id": me.id,
                "phone": me.phone,
                "username": me.username,
                "first_name": me.first_name,
                "last_name": me.last_name,
                "bio": full.full_user.about,
                "status": format_status(me.status),
                "is_online": isinstance(me.status, UserStatusOnline),
                "premium": bool(getattr(me, "premium", False)),
                "tg_created": estimate_tg_created(me.id),
            }
        except Exception as e:
            print(f"[TGManager] Помилка отримання інфо {account_id}: {e}")
            return None


tg_manager = TGManager()
