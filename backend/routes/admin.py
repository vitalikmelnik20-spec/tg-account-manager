import os
import hashlib
import tempfile
from fastapi import APIRouter, Cookie, HTTPException, Response, UploadFile, File, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

router = APIRouter(prefix="/api/auth")
router_admin = APIRouter(prefix="/api/admin")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")


def _token() -> str:
    return hashlib.sha256(f"tgstat-session:{ADMIN_PASSWORD}".encode()).hexdigest()


def check_auth(session: str = Cookie(default=None)):
    if session != _token():
        raise HTTPException(status_code=401, detail="Не авторизований")


class LoginReq(BaseModel):
    password: str


@router.post("/login")
async def login(req: LoginReq, response: Response):
    if req.password != ADMIN_PASSWORD:
        raise HTTPException(401, "Невірний пароль")
    response.set_cookie("session", _token(), httponly=True, max_age=86400 * 30, samesite="lax")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@router.get("/me")
async def me(_=Depends(check_auth)):
    return {"ok": True}


def _db_path() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./tgstat.db")
    return url.split("sqlite+aiosqlite:///")[-1]


@router_admin.get("/download-db")
async def download_db(_=Depends(check_auth)):
    path = _db_path()
    if not os.path.exists(path):
        raise HTTPException(404, "БД не знайдено")
    return FileResponse(path, filename="tgstat.db", media_type="application/octet-stream")


@router_admin.post("/import-db")
async def import_db(file: UploadFile = File(...), _=Depends(check_auth)):
    import aiosqlite
    from backend.database import AsyncSessionLocal
    from backend.models import Account, ReactChannel, MonitoredChannel
    from backend.tg_manager import tg_manager

    content = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        f.write(content)
        tmppath = f.name

    try:
        acc_rows, react_rows, mon_rows = [], [], []
        async with aiosqlite.connect(tmppath) as src:
            src.row_factory = aiosqlite.Row
            for table, bucket in [("accounts", acc_rows), ("react_channels", react_rows), ("monitored_channels", mon_rows)]:
                try:
                    async with src.execute(f"SELECT * FROM {table}") as cur:
                        bucket.extend(await cur.fetchall())
                except Exception:
                    pass

        imported_acc = imported_react = imported_mon = 0

        async with AsyncSessionLocal() as db:
            existing_phones = {r for r in (await db.execute(select(Account.phone))).scalars()}
            for row in acc_rows:
                row = dict(row)
                if row.get("phone") in existing_phones:
                    continue
                db.add(Account(
                    session_string=row.get("session_string", ""),
                    api_id=row.get("api_id", ""),
                    api_hash=row.get("api_hash", ""),
                    phone=row.get("phone"),
                    username=row.get("username"),
                    first_name=row.get("first_name"),
                    last_name=row.get("last_name"),
                    bio=row.get("bio"),
                    pyrogram_session=row.get("pyrogram_session"),
                    twofa_password=row.get("twofa_password"),
                    is_connected=False,
                ))
                existing_phones.add(row.get("phone"))
                imported_acc += 1

            existing_react = {r for r in (await db.execute(select(ReactChannel.channel_id))).scalars()}
            for row in react_rows:
                row = dict(row)
                if row.get("channel_id") in existing_react:
                    continue
                db.add(ReactChannel(
                    channel_id=row.get("channel_id"),
                    access_hash=row.get("access_hash"),
                    username=row.get("username"),
                    title=row.get("title", ""),
                    reaction=row.get("reaction", "👍"),
                    last_msg_id=row.get("last_msg_id", 0),
                    enabled=bool(row.get("enabled", True)),
                ))
                existing_react.add(row.get("channel_id"))
                imported_react += 1

            existing_mon = {r for r in (await db.execute(select(MonitoredChannel.channel_id))).scalars()}
            for row in mon_rows:
                row = dict(row)
                if row.get("channel_id") in existing_mon:
                    continue
                db.add(MonitoredChannel(
                    account_id=row.get("account_id", 0),
                    channel_id=row.get("channel_id"),
                    access_hash=row.get("access_hash"),
                    username=row.get("username"),
                    title=row.get("title", ""),
                    last_msg_id=row.get("last_msg_id", 0),
                    enabled=bool(row.get("enabled", True)),
                ))
                existing_mon.add(row.get("channel_id"))
                imported_mon += 1

            await db.commit()

        await tg_manager.stop()
        await tg_manager.start()

        return {"ok": True, "accounts": imported_acc, "react_channels": imported_react, "monitored_channels": imported_mon}
    finally:
        os.unlink(tmppath)
