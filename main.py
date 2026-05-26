import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.database import init_db
from backend.tg_manager import tg_manager
from backend.routes.accounts import router as accounts_router
from backend.routes.auth import router as auth_router
from backend.routes.profile import router as profile_router
from backend.routes.tg_app import router as tg_app_router
from backend.routes.ws import router as ws_router, manager as ws_manager
from backend.routes.bulk import router as bulk_router
from backend.routes.invite import router as invite_router
from backend.routes.comment import router as comment_router
from backend.routes.react import router as react_router
from backend.routes.admin import router as admin_router, router_admin, _token
from backend.routes.mychannels import router as mychannels_router
from backend.routes.comment_react import router as comment_react_router
from backend.routes.broadcast import router as broadcast_router
from backend.routes.inbox import router as inbox_router
from backend.routes.views import router as views_router

FRONTEND_DIR = Path(__file__).parent / "frontend"


async def _start_accounts():
    await asyncio.sleep(40)
    await tg_manager.start()


async def _subscriber_history_task():
    """Every 30 min save subscriber count snapshots for all admin channels."""
    from backend.routes.mychannels import collect_all_snapshots
    await asyncio.sleep(60)  # Wait for accounts to connect first
    while True:
        try:
            await collect_all_snapshots()
            print("[history] subscriber snapshots saved")
        except Exception as e:
            print(f"[history] error: {e}")
        await asyncio.sleep(1800)  # 30 minutes


async def _report_scheduler_task():
    """Send daily/weekly/monthly reports to Saved Messages at 00:00 Kyiv."""
    from datetime import timezone, timedelta
    from backend.routes.reports import send_reports
    await asyncio.sleep(90)  # Wait for accounts to connect first
    _KYIV = timezone(timedelta(hours=3))
    last_sent_date = None
    while True:
        try:
            now = __import__('datetime').datetime.now(_KYIV)
            today = now.date()
            if now.hour == 0 and now.minute < 3 and last_sent_date != today:
                last_sent_date = today
                await send_reports('day')
                if now.weekday() == 0:   # Monday → weekly
                    await send_reports('week')
                if now.day == 1:         # 1st of month → monthly
                    await send_reports('month')
        except Exception as e:
            print(f"[report-scheduler] error: {e}")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    tg_manager.set_broadcaster(ws_manager.broadcast)
    asyncio.create_task(_start_accounts())
    asyncio.create_task(_subscriber_history_task())
    asyncio.create_task(_report_scheduler_task())
    yield
    await tg_manager.stop()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/auth/"):
        session = request.cookies.get("session")
        if session != _token():
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)

app.include_router(accounts_router)
app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(tg_app_router)
app.include_router(ws_router)
app.include_router(bulk_router)
app.include_router(invite_router)
app.include_router(comment_router)
app.include_router(react_router)
app.include_router(admin_router)
app.include_router(router_admin)
app.include_router(mychannels_router)
app.include_router(comment_react_router)
app.include_router(broadcast_router)
app.include_router(inbox_router)
app.include_router(views_router)


@app.get("/login")
async def login_page():
    return FileResponse(str(FRONTEND_DIR / "login.html"))


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
