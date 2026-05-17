import re
import uuid
import aiohttp
from typing import Dict

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "https://my.telegram.org/",
}
_BASE = "https://my.telegram.org"

# temp_id → {session, phone, random_hash}
_pending: Dict[str, dict] = {}


async def send_code(phone: str) -> str:
    """Відправляє код на телефон через my.telegram.org. Повертає temp_id."""
    session = aiohttp.ClientSession(headers=_HEADERS)
    try:
        # Visit homepage first to obtain session cookies
        await session.get(f"{_BASE}/auth")

        resp = await session.post(
            f"{_BASE}/auth/send_password",
            data={"phone": phone},
            headers={"Referer": f"{_BASE}/auth"},
        )
        raw = await resp.text()
        if not raw or raw.strip() == "":
            await session.close()
            raise ValueError("my.telegram.org повернув порожню відповідь — спробуй ще раз")
        try:
            import json
            data = json.loads(raw)
        except Exception:
            await session.close()
            if "too many" in raw.lower():
                raise ValueError("my.telegram.org: забагато спроб — зачекай 10–30 хвилин і спробуй знову")
            preview = raw[:200].replace("\n", " ")
            raise ValueError(f"my.telegram.org повернув не JSON: {preview}")
    except ValueError:
        raise
    except Exception as e:
        await session.close()
        raise ValueError(f"Помилка з'єднання з my.telegram.org: {e}")

    random_hash = data.get("random_hash")
    if not random_hash:
        await session.close()
        error = data.get("error", str(data))
        raise ValueError(f"my.telegram.org: {error}")

    temp_id = str(uuid.uuid4())
    _pending[temp_id] = {"session": session, "phone": phone, "random_hash": random_hash}
    return temp_id


async def verify_and_get_credentials(temp_id: str, code: str) -> dict:
    """Верифікує код, отримує або створює app, повертає api_id та api_hash."""
    entry = _pending.pop(temp_id, None)
    if not entry:
        raise ValueError("Сесія не знайдена або вже завершена")

    session: aiohttp.ClientSession = entry["session"]
    phone = entry["phone"]
    random_hash = entry["random_hash"]

    try:
        # 1. Логін
        resp = await session.post(
            f"{_BASE}/auth/login",
            data={"phone": phone, "random_hash": random_hash, "password": code},
        )
        text = await resp.text()
        if "true" not in text.lower():
            raise ValueError("Невірний код або помилка входу")

        # 2. Сторінка додатків
        resp = await session.get(f"{_BASE}/apps")
        html = await resp.text()

        creds = _parse_credentials(html)
        if creds:
            return {**creds, "created": False}

        # 3. Додаток не існує — створюємо
        form_hash = _parse_form_hash(html)
        short = f"app{uuid.uuid4().hex[:6]}"
        await session.post(
            f"{_BASE}/apps/create",
            data={
                "hash": form_hash,
                "app_title": "MyApp",
                "app_shortname": short,
                "app_description": "",
                "app_platform": "other",
                "app_platforms": "other",
            },
        )

        # 4. Читаємо знову
        resp = await session.get(f"{_BASE}/apps")
        html = await resp.text()
        creds = _parse_credentials(html)
        if creds:
            return {**creds, "created": True}

        raise ValueError("Не вдалось отримати api_id і api_hash — спробуй ще раз або перевір акаунт на my.telegram.org вручну")

    finally:
        await session.close()


async def cancel(temp_id: str):
    entry = _pending.pop(temp_id, None)
    if entry:
        try:
            await entry["session"].close()
        except Exception:
            pass


# ── helpers ──

def _parse_credentials(html: str) -> dict | None:
    # api_id — ціле число поряд з підписом
    id_match = re.search(
        r'(?:api_id|App api_id)[^<]*</\w+>\s*<[^>]+>\s*(\d{4,12})\s*<',
        html, re.IGNORECASE,
    )
    # api_hash — 32-символьний hex
    hash_match = re.search(r'\b([a-f0-9]{32})\b', html)

    if id_match and hash_match:
        return {"api_id": id_match.group(1), "api_hash": hash_match.group(1)}

    # запасний варіант: шукаємо обидва в form-control-static
    spans = re.findall(r'<span[^>]*form-control-static[^>]*>\s*([^<]+?)\s*</span>', html)
    api_id = next((s.strip() for s in spans if s.strip().isdigit() and len(s.strip()) >= 4), None)
    api_hash = next((s.strip() for s in spans if re.fullmatch(r'[a-f0-9]{32}', s.strip())), None)
    if api_id and api_hash:
        return {"api_id": api_id, "api_hash": api_hash}

    return None


def _parse_form_hash(html: str) -> str:
    m = re.search(r'<input[^>]+name=["\']hash["\'][^>]+value=["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)
    m = re.search(r'["\']hash["\']\s*:\s*["\']([^"\']+)["\']', html)
    return m.group(1) if m else ""
