"""FastAPI 应用入口。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import FILES_DIR, STATIC_DIR, ensure_dirs
from .database import Base, SessionLocal, engine
from .routers import api
from .seed import seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    yield


app = FastAPI(title="手术知情同意版本与撤回系统", lifespan=lifespan)
app.include_router(api)

# 静态资源：前端页面 + 本地生成的同意书文件（历史文件只读保留）
app.mount("/files", StaticFiles(directory=FILES_DIR), name="files")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
