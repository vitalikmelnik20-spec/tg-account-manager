import io
import json
import uuid
import asyncio
from fastapi import APIRouter, UploadFile, File, Form
from typing import Optional

from backend.tg_manager import tg_manager

from telethon.tl.functions.channels import (
    CreateChannelRequest, EditPhotoRequest,
    UpdateUsernameRequest as ChUsernameReq,
    JoinChannelRequest,
)
from telethon.tl.functions.messages import ExportChatInviteRequest
from telethon.tl.types import InputChatUploadedPhoto, InputChannel, InputPeerChannel

router = APIRouter(prefix="/api/bulk")


def _random_username() -> str:
    return "ch_" + uuid.uuid4().hex[:9]


async def _create_one(
    account_id: int, title: str, about: str,
    username_mode: str, username_val: str,
    photo_data: Optional[bytes], photo_name: str,
    add_to_profile: bool,
) -> dict:
    client = tg_manager.clients.get(account_id)
    if not client:
        return {"account_id": account_id, "success": False, "error": "Акаунт не підключено"}
    try:
        result = await client(CreateChannelRequest(title=title, about=about, megagroup=False))
        channel = result.chats[0]

        uname = username_val if (username_mode == "manual" and username_val) else _random_username()
        link = None
        try:
            await client(ChUsernameReq(channel=channel, username=uname))
            link = f"https://t.me/{uname}"
        except Exception:
            uname = None
            try:
                invite = await client(ExportChatInviteRequest(peer=channel))
                link = invite.link
            except Exception:
                link = None

        if photo_data:
            try:
                uploaded = await client.upload_file(io.BytesIO(photo_data), file_name=photo_name)
                await client(EditPhotoRequest(
                    channel=InputChannel(channel.id, channel.access_hash),
                    photo=InputChatUploadedPhoto(file=uploaded),
                ))
            except Exception:
                pass

        if add_to_profile:
            try:
                from telethon.tl.functions.account import UpdatePersonalChannelRequest
                await client(UpdatePersonalChannelRequest(
                    channel=InputChannel(channel.id, channel.access_hash)
                ))
            except Exception:
                pass

        return {
            "account_id": account_id, "success": True,
            "channel_id": channel.id, "access_hash": channel.access_hash,
            "username": uname, "link": link,
        }
    except Exception as e:
        return {"account_id": account_id, "success": False, "error": str(e)}


async def _resolve_entity(client, channel_id: int, access_hash: int, username: str = None):
    """Отримує entity для каналу кількома способами."""
    # 1. Спробуємо через username якщо є
    if username:
        try:
            return await client.get_entity(username)
        except Exception:
            pass
    # 2. Через InputPeerChannel
    try:
        return await client.get_entity(InputPeerChannel(channel_id, access_hash))
    except Exception:
        pass
    # 3. Шукаємо в діалогах
    from telethon.tl.types import PeerChannel
    try:
        return await client.get_entity(PeerChannel(channel_id))
    except Exception:
        pass
    async for dialog in client.iter_dialogs(limit=300):
        if getattr(dialog.entity, 'id', None) == channel_id:
            return dialog.entity
    raise ValueError(f"Канал {channel_id} не знайдено")


async def _post_one(
    account_id: int, channel_id: int, access_hash: int,
    text: str, photo_data: Optional[bytes], photo_name: str,
    username: str = None,
) -> dict:
    client = tg_manager.clients.get(account_id)
    if not client:
        return {"account_id": account_id, "channel_id": channel_id, "success": False, "error": "Акаунт не підключено"}
    try:
        entity = await _resolve_entity(client, channel_id, access_hash, username)
        if photo_data:
            msg = await client.send_file(entity, io.BytesIO(photo_data), caption=text, file_name=photo_name)
        else:
            msg = await client.send_message(entity, text)
        return {"account_id": account_id, "channel_id": channel_id, "success": True, "message_id": msg.id}
    except Exception as e:
        return {"account_id": account_id, "channel_id": channel_id, "success": False, "error": str(e)}


async def _join_one(account_id: int, link: str) -> dict:
    from telethon.errors import InviteRequestSentError, UserAlreadyParticipantError
    client = tg_manager.clients.get(account_id)
    if not client:
        return {"account_id": account_id, "success": False, "error": "Акаунт не підключено"}
    try:
        entity = await client.get_entity(link)
        await client(JoinChannelRequest(entity))
        title = getattr(entity, 'title', link)
        return {"account_id": account_id, "success": True, "title": title}
    except InviteRequestSentError:
        return {"account_id": account_id, "success": True, "title": link, "note": "Заявка відправлена, очікує схвалення"}
    except UserAlreadyParticipantError:
        return {"account_id": account_id, "success": True, "title": link, "note": "Вже підписаний"}
    except Exception as e:
        return {"account_id": account_id, "success": False, "error": str(e)}


@router.post("/create-channel")
async def bulk_create_channel(
    title: str = Form(...),
    about: str = Form(""),
    username_mode: str = Form("random"),
    username_val: str = Form(""),
    add_to_profile: str = Form("false"),
    account_ids: str = Form("[]"),
    photo: Optional[UploadFile] = File(None),
):
    photo_data = await photo.read() if photo else None
    photo_name = (photo.filename or "photo.jpg") if photo else "photo.jpg"
    ids = json.loads(account_ids) or list(tg_manager.clients.keys())
    results = await asyncio.gather(*[
        _create_one(aid, title, about, username_mode, username_val, photo_data, photo_name, add_to_profile.lower() == "true")
        for aid in ids
    ])
    return {"results": list(results)}


@router.post("/post")
async def bulk_post(
    text: str = Form(""),
    channels: str = Form("[]"),
    photo: Optional[UploadFile] = File(None),
):
    photo_data = await photo.read() if photo else None
    photo_name = (photo.filename or "photo.jpg") if photo else "photo.jpg"
    ch_list = json.loads(channels)
    results = await asyncio.gather(*[
        _post_one(c["account_id"], c["channel_id"], c["access_hash"], text, photo_data, photo_name, c.get("username"))
        for c in ch_list
    ])
    return {"results": list(results)}


@router.get("/channels")
async def all_channels():
    """Повертає всі канали з усіх підключених акаунтів."""
    from telethon.tl.functions.channels import GetFullChannelRequest
    results = []

    async def _get(account_id):
        client = tg_manager.clients.get(account_id)
        if not client:
            return
        try:
            async for dialog in client.iter_dialogs(limit=300):
                e = dialog.entity
                if not getattr(e, 'broadcast', False):
                    continue
                if not (getattr(e, 'creator', False) or getattr(e, 'admin_rights', None)):
                    continue
                username = getattr(e, 'username', None)
                results.append({
                    "account_id": account_id,
                    "channel_id": e.id,
                    "access_hash": e.access_hash,
                    "title": e.title,
                    "username": username,
                    "link": f"https://t.me/{username}" if username else None,
                })
        except Exception:
            pass

    await asyncio.gather(*[_get(aid) for aid in tg_manager.clients.keys()])
    return results


@router.post("/join")
async def bulk_join(
    link: str = Form(...),
    account_ids: str = Form("[]"),
):
    ids = json.loads(account_ids) or list(tg_manager.clients.keys())
    results = await asyncio.gather(*[_join_one(aid, link.strip()) for aid in ids])
    return {"results": list(results)}
