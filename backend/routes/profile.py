import io
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from backend.database import get_db
from backend.models import Account
from backend.tg_manager import tg_manager

from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    EditPhotoRequest,
    GetFullChannelRequest,
    JoinChannelRequest,
)
from telethon.tl.functions.messages import ExportChatInviteRequest
from telethon.tl.types import InputChatUploadedPhoto, InputChannel

router = APIRouter(prefix="/api/accounts")


async def _resolve_channel(client, channel_id: int):
    """Шукає канал спочатку в кеші сесії, потім в діалогах."""
    from telethon.tl.types import PeerChannel
    try:
        return await client.get_entity(PeerChannel(channel_id))
    except Exception:
        pass
    async for dialog in client.iter_dialogs(limit=300):
        if getattr(dialog.entity, 'id', None) == channel_id:
            return dialog.entity
    raise ValueError(f"Канал {channel_id} не знайдено")


# ───────────────────────── schemas ─────────────────────────

class UpdateProfileReq(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    bio: Optional[str] = None
    username: Optional[str] = None


class CreateChannelReq(BaseModel):
    title: str
    about: str = ""
    username: str = ""
    megagroup: bool = False


class JoinReq(BaseModel):
    link: str   # @username, t.me/... або invite link


class PostReq(BaseModel):
    text: str


# ───────────────────────── profile ─────────────────────────

@router.patch("/{account_id}/profile")
async def update_profile(account_id: int, req: UpdateProfileReq, db: AsyncSession = Depends(get_db)):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")

    me = await client.get_me()

    if req.first_name is not None or req.last_name is not None or req.bio is not None:
        await client(UpdateProfileRequest(
            first_name=req.first_name if req.first_name is not None else (me.first_name or ""),
            last_name=req.last_name if req.last_name is not None else (me.last_name or ""),
            about=req.bio if req.bio is not None else "",
        ))

    if req.username is not None:
        current = (me.username or "").lower()
        new = req.username.lower()
        if new != current:
            try:
                await client(UpdateUsernameRequest(username=req.username))
            except Exception as e:
                raise HTTPException(400, f"Не вдалось змінити username: {e}")

    account = await db.get(Account, account_id)
    if account:
        me = await client.get_me()
        account.first_name = me.first_name
        account.last_name = me.last_name
        account.username = me.username
        await db.commit()

    return {"ok": True}


@router.post("/{account_id}/photo")
async def update_profile_photo(account_id: int, file: UploadFile = File(...)):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")

    data = await file.read()
    uploaded = await client.upload_file(io.BytesIO(data), file_name=file.filename or "photo.jpg")
    from telethon.tl.functions.photos import UploadProfilePhotoRequest
    await client(UploadProfilePhotoRequest(file=uploaded))
    return {"ok": True}


# ───────────────────────── channels list ───────────────────

@router.get("/{account_id}/channels")
async def get_channels(account_id: int):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")

    channels = []
    async for dialog in client.iter_dialogs(limit=300):
        e = dialog.entity
        is_channel = getattr(e, 'broadcast', False)
        is_group = getattr(e, 'megagroup', False)
        is_creator = getattr(e, 'creator', False)
        admin_rights = getattr(e, 'admin_rights', None)
        if not (is_channel or is_group):
            continue
        if not (is_creator or admin_rights):
            continue
        try:
            full = await client(GetFullChannelRequest(e))
            subs = full.full_chat.participants_count or 0
        except Exception:
            subs = 0

        username = getattr(e, 'username', None)
        link = f"https://t.me/{username}" if username else None

        channels.append({
            "id": e.id,
            "access_hash": e.access_hash,
            "title": e.title,
            "username": username,
            "subscribers": subs,
            "is_creator": is_creator,
            "is_megagroup": is_group,
            "link": link,
        })

    return channels


# ───────────────────────── create channel ──────────────────

@router.post("/{account_id}/channel")
async def create_channel(account_id: int, req: CreateChannelReq):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")

    try:
        result = await client(CreateChannelRequest(
            title=req.title,
            about=req.about,
            megagroup=req.megagroup,
        ))
        channel = result.chats[0]

        link = None
        if req.username:
            from telethon.tl.functions.channels import UpdateUsernameRequest as ChUsernameReq
            try:
                await client(ChUsernameReq(channel=channel, username=req.username))
                link = f"https://t.me/{req.username}"
            except Exception as e:
                raise HTTPException(400, f"Канал створено, але username зайнятий: {e}")
        else:
            try:
                invite = await client(ExportChatInviteRequest(peer=channel))
                link = invite.link
            except Exception:
                link = None

        return {
            "ok": True,
            "id": channel.id,
            "access_hash": channel.access_hash,
            "title": channel.title,
            "link": link,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Не вдалось створити канал: {e}")


# ───────────────────────── channel photo ───────────────────

@router.post("/{account_id}/channels/{channel_id}/photo")
async def set_channel_photo(
    account_id: int,
    channel_id: int,
    file: UploadFile = File(...),
    access_hash: int = Form(0),
):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")

    data = await file.read()
    try:
        if access_hash:
            entity = InputChannel(channel_id, access_hash)
        else:
            # Шукаємо в діалогах — повільніше, але надійно
            entity = None
            async for dialog in client.iter_dialogs(limit=300):
                if getattr(dialog.entity, 'id', None) == channel_id:
                    entity = dialog.entity
                    break
            if entity is None:
                raise ValueError(f"Канал {channel_id} не знайдено в діалогах")

        uploaded = await client.upload_file(io.BytesIO(data), file_name=file.filename or "photo.jpg")
        await client(EditPhotoRequest(
            channel=entity,
            photo=InputChatUploadedPhoto(file=uploaded),
        ))
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, f"Не вдалось встановити фото: {e}")


# ───────────────────────── set personal channel ────────────

class ChannelWithHashReq(BaseModel):
    access_hash: int = 0


@router.patch("/{account_id}/channels/{channel_id}/personal")
async def set_personal_channel(account_id: int, channel_id: int, req: ChannelWithHashReq = ChannelWithHashReq()):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")

    try:
        from telethon.tl.functions.account import UpdatePersonalChannelRequest
        entity = InputChannel(channel_id, req.access_hash) if req.access_hash else await _resolve_channel(client, channel_id)
        await client(UpdatePersonalChannelRequest(channel=entity))
        return {"ok": True}
    except ImportError:
        raise HTTPException(400, "Ця версія Telethon не підтримує особистий канал")
    except Exception as e:
        raise HTTPException(400, f"Не вдалось встановити особистий канал: {e}")


# ───────────────────────── create post ────────────────────

@router.post("/{account_id}/channels/{channel_id}/post")
async def create_post(account_id: int, channel_id: int, req: PostReq):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")
    try:
        entity = await _resolve_channel(client, channel_id)
        msg = await client.send_message(entity, req.text)
        return {"ok": True, "message_id": msg.id}
    except Exception as e:
        raise HTTPException(400, f"Не вдалось опублікувати пост: {e}")


@router.post("/{account_id}/channels/{channel_id}/post-with-photo")
async def create_post_with_photo(
    account_id: int, channel_id: int,
    text: str = Form(""),
    file: UploadFile = File(...),
):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")
    data = await file.read()
    try:
        entity = await _resolve_channel(client, channel_id)
        msg = await client.send_file(entity, io.BytesIO(data), caption=text, file_name=file.filename)
        return {"ok": True, "message_id": msg.id}
    except Exception as e:
        raise HTTPException(400, f"Не вдалось опублікувати пост: {e}")


# ───────────────────────── join channel ───────────────────

@router.post("/{account_id}/join")
async def join_channel(account_id: int, req: JoinReq):
    client = tg_manager.clients.get(account_id)
    if not client:
        raise HTTPException(400, "Акаунт не підключено")

    link = req.link.strip()
    try:
        entity = await client.get_entity(link)
        await client(JoinChannelRequest(entity))
        title = getattr(entity, 'title', link)
        username = getattr(entity, 'username', None)
        return {"ok": True, "title": title, "username": username}
    except Exception as e:
        raise HTTPException(400, f"Не вдалось підписатись: {e}")
