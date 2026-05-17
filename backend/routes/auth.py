from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.database import get_db
from backend.models import Account
from backend.tg_manager import tg_manager
import backend.auth_manager as auth_manager

router = APIRouter(prefix="/api/auth")


class StartReq(BaseModel):
    phone: str
    api_id: str
    api_hash: str
    account_id: int | None = None  # якщо задано — оновлюємо сесію існуючого акаунту


class CodeReq(BaseModel):
    temp_id: str
    code: str


class TwoFAReq(BaseModel):
    temp_id: str
    password: str


class CancelReq(BaseModel):
    temp_id: str


@router.post("/start")
async def start(req: StartReq):
    try:
        temp_id = await auth_manager.start_auth(
            req.phone.strip(), int(req.api_id.strip()), req.api_hash.strip(),
            account_id=req.account_id,
        )
        return {"temp_id": temp_id}
    except Exception as e:
        raise HTTPException(400, f"Не вдалось відправити код: {e}")


@router.post("/verify-code")
async def verify_code(req: CodeReq, db: AsyncSession = Depends(get_db)):
    try:
        result = await auth_manager.verify_code(req.temp_id, req.code.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Невірний код: {e}")

    if result.get("needs_2fa"):
        return {"needs_2fa": True}

    return await _save_account(result, db)


@router.post("/verify-2fa")
async def verify_2fa(req: TwoFAReq, db: AsyncSession = Depends(get_db)):
    try:
        result = await auth_manager.verify_2fa(req.temp_id, req.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Невірний пароль 2FA: {e}")

    return await _save_account(result, db)


@router.post("/cancel")
async def cancel(req: CancelReq):
    await auth_manager.cancel(req.temp_id)
    return {"ok": True}


async def _save_account(result: dict, db: AsyncSession) -> dict:
    from backend.tg_manager import telethon_to_pyrogram

    session = result["session_string"]
    pyro = telethon_to_pyrogram(session)
    account_id = result.get("account_id")

    if account_id:
        account = await db.get(Account, account_id)
        if not account:
            raise HTTPException(404, "Акаунт не знайдено")
        await tg_manager.disconnect_account(account_id)
        account.session_string = session
        account.pyrogram_session = pyro
        await db.commit()
        await tg_manager.connect_account(account_id, session, int(account.api_id), account.api_hash)
        return {"done": True, "id": account_id, "refreshed": True}

    account = Account(
        session_string=session,
        pyrogram_session=pyro,
        api_id=str(result["api_id"]),
        api_hash=result["api_hash"],
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    await tg_manager.connect_account(
        account.id, account.session_string, int(account.api_id), account.api_hash
    )
    await db.refresh(account)
    return {"done": True, "id": account.id}
