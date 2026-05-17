import uuid
from typing import Dict
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

# temp_id → {client, phone, api_id, api_hash, phone_code_hash}
_pending: Dict[str, dict] = {}


async def start_auth(phone: str, api_id: int, api_hash: str, account_id: int | None = None) -> str:
    """Відправляє код на телефон. Повертає temp_id."""
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    result = await client.send_code_request(phone)

    temp_id = str(uuid.uuid4())
    _pending[temp_id] = {
        "client": client,
        "phone": phone,
        "api_id": api_id,
        "api_hash": api_hash,
        "phone_code_hash": result.phone_code_hash,
        "account_id": account_id,
        "code": None,
    }
    return temp_id


async def verify_code(temp_id: str, code: str) -> dict:
    """
    Підтверджує код.
    Повертає:
      {"done": True, "session_string": ..., "api_id": ..., "api_hash": ...}
      {"needs_2fa": True}  — якщо потрібен пароль 2FA
    """
    session = _pending.get(temp_id)
    if not session:
        raise ValueError("Сесія не знайдена або вже завершена")

    client: TelegramClient = session["client"]
    session["code"] = code  # зберігаємо для verify_2fa

    try:
        await client.sign_in(session["phone"], code, phone_code_hash=session["phone_code_hash"])
    except SessionPasswordNeededError:
        return {"needs_2fa": True}

    return await _finish(temp_id)


async def verify_2fa(temp_id: str, password: str) -> dict:
    """Підтверджує пароль 2FA після verify_code → needs_2fa."""
    session = _pending.get(temp_id)
    if not session:
        raise ValueError("Сесія не знайдена або вже завершена")

    await session["client"].sign_in(password=password)
    return await _finish(temp_id)


async def _finish(temp_id: str) -> dict:
    session = _pending.pop(temp_id)
    client: TelegramClient = session["client"]
    session_string = client.session.save()
    await client.disconnect()
    return {
        "done": True,
        "session_string": session_string,
        "api_id": session["api_id"],
        "api_hash": session["api_hash"],
        "account_id": session.get("account_id"),
    }


async def cancel(temp_id: str):
    session = _pending.pop(temp_id, None)
    if session:
        try:
            await session["client"].disconnect()
        except Exception:
            pass
