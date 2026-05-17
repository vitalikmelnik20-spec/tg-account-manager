from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import backend.my_tg_api as my_tg_api

router = APIRouter(prefix="/api/tg-app")


class PhoneReq(BaseModel):
    phone: str


class VerifyReq(BaseModel):
    temp_id: str
    code: str


class CancelReq(BaseModel):
    temp_id: str


@router.post("/send-code")
async def send_code(req: PhoneReq):
    try:
        temp_id = await my_tg_api.send_code(req.phone.strip())
        return {"temp_id": temp_id}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/verify")
async def verify(req: VerifyReq):
    try:
        result = await my_tg_api.verify_and_get_credentials(req.temp_id, req.code.strip())
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/cancel")
async def cancel(req: CancelReq):
    await my_tg_api.cancel(req.temp_id)
    return {"ok": True}
