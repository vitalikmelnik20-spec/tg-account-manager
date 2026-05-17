import asyncio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from backend.database import get_db
from backend.models import Account, OTPCode
from backend.tg_manager import tg_manager, normalize_session, telethon_to_pyrogram

router = APIRouter(prefix="/api")


class AddAccountRequest(BaseModel):
    session_string: str
    api_id: str
    api_hash: str
    twofa_password: str = ""


class Set2FARequest(BaseModel):
    twofa_password: str


@router.post("/accounts")
async def add_account(req: AddAccountRequest, db: AsyncSession = Depends(get_db)):
    try:
        session = normalize_session(req.session_string)
        pyro_session = telethon_to_pyrogram(session)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    account = Account(
        session_string=session,
        pyrogram_session=pyro_session,
        api_id=req.api_id.strip(),
        api_hash=req.api_hash.strip(),
        twofa_password=req.twofa_password.strip() or None,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    success = await tg_manager.connect_account(
        account.id, account.session_string, int(account.api_id), account.api_hash
    )

    if not success:
        await db.delete(account)
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail="Не вдалось підключити акаунт — перевір session string, api_id та api_hash",
        )

    await db.refresh(account)
    return {"id": account.id, "status": "connected"}


@router.get("/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).order_by(Account.created_at.desc()))
    accounts = result.scalars().all()
    return [
        {
            "id": a.id,
            "phone": a.phone,
            "username": a.username,
            "first_name": a.first_name,
            "last_name": a.last_name,
            "is_connected": a.id in tg_manager.clients,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in accounts
    ]


@router.get("/accounts/{account_id}")
async def get_account(account_id: int, db: AsyncSession = Depends(get_db)):
    account = await db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Акаунт не знайдено")

    credentials = {
        "api_id": account.api_id,
        "api_hash": account.api_hash,
        "session_string": account.session_string,
        "pyrogram_session": account.pyrogram_session or "",
        "twofa_password": account.twofa_password or "",
    }

    info = await tg_manager.get_full_info(account_id)
    if info:
        return {
            "id": account_id,
            "is_connected": True,
            "created_at": account.created_at.isoformat() if account.created_at else None,
            **info,
            **credentials,
        }

    return {
        "id": account_id,
        "tg_id": None,
        "phone": account.phone,
        "username": account.username,
        "first_name": account.first_name,
        "last_name": account.last_name,
        "bio": account.bio,
        "status": "Відключено",
        "is_online": False,
        "is_connected": False,
        "premium": False,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        **credentials,
    }


@router.get("/accounts/{account_id}/photo")
async def get_photo(account_id: int):
    photo_bytes = await tg_manager.get_profile_photo(account_id)
    if not photo_bytes:
        raise HTTPException(404, "Фото не знайдено")
    return Response(content=photo_bytes, media_type="image/jpeg")


@router.get("/accounts/{account_id}/codes")
async def get_codes(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(OTPCode)
        .where(OTPCode.account_id == account_id)
        .order_by(OTPCode.received_at.desc())
        .limit(50)
    )
    codes = result.scalars().all()
    return [
        {
            "id": c.id,
            "code": c.code,
            "code_type": c.code_type or "login",
            "message": c.message_text,
            "received_at": c.received_at.isoformat() if c.received_at else None,
        }
        for c in codes
    ]


@router.get("/accounts/{account_id}/status")
async def get_status(account_id: int):
    return await tg_manager.check_status(account_id)


class RefreshSessionRequest(BaseModel):
    session_string: str


@router.post("/accounts/{account_id}/clone-session")
async def clone_session(account_id: int, db: AsyncSession = Depends(get_db)):
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession
    from telethon.tl.functions.auth import ExportLoginTokenRequest, AcceptLoginTokenRequest, ImportLoginTokenRequest
    from telethon.tl.types import UpdateLoginToken
    from telethon.tl.types.auth import LoginTokenMigrateTo, LoginTokenSuccess

    account = await db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Акаунт не знайдено")

    existing_client = tg_manager.clients.get(account_id)
    if not existing_client:
        raise HTTPException(400, "Акаунт не підключено — спочатку перепідключи")

    api_id = int(account.api_id)
    api_hash = account.api_hash

    new_client = TelegramClient(StringSession(), api_id, api_hash)
    update_received = asyncio.Event()

    @new_client.on(events.Raw(UpdateLoginToken))
    async def _on_update(_):
        update_received.set()

    try:
        await new_client.connect()

        # 1. Отримуємо токен
        export = await new_client(ExportLoginTokenRequest(
            api_id=api_id, api_hash=api_hash, except_ids=[]
        ))
        token = export.token

        # 2. Існуючий клієнт підтверджує
        await existing_client(AcceptLoginTokenRequest(token=token))

        # 3. Чекаємо UpdateLoginToken
        try:
            await asyncio.wait_for(update_received.wait(), timeout=15)
        except asyncio.TimeoutError:
            raise HTTPException(500, "Telegram не відповів за 15 секунд")

        # 4. Після UpdateLoginToken — викликаємо ExportLoginToken ЗНОВУ
        #    (не ImportLoginToken зі старим токеном)
        result = await new_client(ExportLoginTokenRequest(
            api_id=api_id, api_hash=api_hash, except_ids=[]
        ))

        # Якщо потрібна міграція DC
        if isinstance(result, LoginTokenMigrateTo):
            await new_client._switch_dc(result.dc_id)
            try:
                result = await new_client(ImportLoginTokenRequest(token=result.token))
            except Exception as e:
                if "password" in str(e).lower() or "two" in str(e).lower():
                    # 2FA потрібна — використовуємо збережений пароль
                    pwd = account.twofa_password
                    if not pwd:
                        raise HTTPException(400, "Акаунт має 2FA але пароль не збережено в системі. Додай 2FA пароль у профілі акаунта.")
                    await new_client.sign_in(password=pwd)
                    result = LoginTokenSuccess(authorization=None)
                else:
                    raise

        if not isinstance(result, LoginTokenSuccess):
            raise HTTPException(500, f"Невідомий результат: {type(result).__name__}")

        new_session = new_client.session.save()
        pyro = telethon_to_pyrogram(new_session)
        return {"session_string": new_session, "pyrogram_session": pyro}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Помилка клонування: {e}")
    finally:
        await new_client.disconnect()


@router.post("/accounts/{account_id}/reconnect")
async def reconnect(account_id: int, db: AsyncSession = Depends(get_db)):
    account = await db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Акаунт не знайдено")
    await tg_manager.disconnect_account(account_id)
    success = await tg_manager.connect_account(
        account_id, account.session_string, int(account.api_id), account.api_hash
    )
    if not success:
        raise HTTPException(400, "Не вдалось перепідключитись — сесія недійсна, потрібно оновити")
    return {"ok": True}


@router.patch("/accounts/{account_id}/session")
async def refresh_session(account_id: int, req: RefreshSessionRequest, db: AsyncSession = Depends(get_db)):
    account = await db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Акаунт не знайдено")
    try:
        session = normalize_session(req.session_string)
        pyro_session = telethon_to_pyrogram(session)
    except ValueError as e:
        raise HTTPException(400, str(e))

    await tg_manager.disconnect_account(account_id)

    account.session_string = session
    account.pyrogram_session = pyro_session
    await db.commit()

    success = await tg_manager.connect_account(account_id, session, int(account.api_id), account.api_hash)
    if not success:
        raise HTTPException(400, "Не вдалось підключити акаунт з новою сесією")
    return {"ok": True}


@router.patch("/accounts/{account_id}/2fa")
async def set_2fa(account_id: int, req: Set2FARequest, db: AsyncSession = Depends(get_db)):
    account = await db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Акаунт не знайдено")
    account.twofa_password = req.twofa_password.strip() or None
    await db.commit()
    return {"ok": True}


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    account = await db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Акаунт не знайдено")
    await tg_manager.disconnect_account(account_id)
    await db.delete(account)
    await db.commit()
    return {"status": "deleted"}
