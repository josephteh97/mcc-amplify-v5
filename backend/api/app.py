"""FastAPI application entry point.

Run with:
    uvicorn backend.api.app:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from backend.api.jobs import JOBS_ROOT, MAX_CONCURRENT_JOBS
from backend.api.routes import router


@asynccontextmanager
async def lifespan(_: FastAPI):
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    logger.info(f"v5.3 API starting — jobs_root={JOBS_ROOT} max_concurrent={MAX_CONCURRENT_JOBS}")
    yield
    logger.info("v5.3 API shutting down")


app = FastAPI(title="MCC Amplify v5.3", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
