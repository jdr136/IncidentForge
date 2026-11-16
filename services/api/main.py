"""IncidentForge API — entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from app.api.routes import benchmarks, health, providers, runs, scenarios
from app.persistence.database import init_db
from app.persistence.run_repository import RunRepository
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("IncidentForge API starting up")
    await init_db()
    recovered = await RunRepository().mark_all_interrupted()
    if recovered:
        logger.warning("Recovered %d interrupted run(s): %s", len(recovered), recovered)
    yield
    logger.info("IncidentForge API shutting down")


app = FastAPI(
    title="IncidentForge",
    version="0.1.0",
    description="AI incident response evaluation lab",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(scenarios.router, prefix="/api/v1")
app.include_router(providers.router, prefix="/api/v1")
app.include_router(runs.router, prefix="/api/v1")
app.include_router(benchmarks.router, prefix="/api/v1")
