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

FRONTEND_DIR = Path(__file__).parent / "frontend"


async def _start_accounts():
    import asyncio
    await asyncio.sleep(2)
    await tg_manager.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    tg_manager.set_broadcaster(ws_manager.broadcast)
    asyncio.create_task(_start_accounts())
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


@app.get("/login")
async def login_page():
    return FileResponse(str(FRONTEND_DIR / "login.html"))


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
