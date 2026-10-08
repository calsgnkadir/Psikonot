"""
backend/main.py — PsikoNot · Backend API v5.0.0
======================================================
"""

import os
import sys
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Path Configuration
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_PROJECT_ROOT, ".env"))
except ImportError:
    # Fallback to robust manual parsing
    _dotenv_path = os.path.join(_PROJECT_ROOT, ".env")
    if os.path.exists(_dotenv_path):
        with open(_dotenv_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#"):
                    continue
                if "=" in _line:
                    _key, _val = _line.split("=", 1)
                    os.environ[_key.strip()] = _val.strip().strip('"').strip("'")


from core.security import get_device_id
import database.storage as storage

# Middlewares
from backend.middleware.csrf import CSRFMiddleware
from backend.middleware.rate_limiter import RateLimiterMiddleware
from backend.middleware.xss_protection import XSSProtectionMiddleware
from backend.middleware.ip_allowlist import IPAllowlistMiddleware

# Routers
from backend.routers.auth import router as auth_router
from backend.routers.admin import router as admin_router
from backend.routers.records import router as records_router
from backend.routers.misc import router as misc_router
from backend.routers.alerts import router as alerts_router

import logging
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("VIPHealthVault")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown — replaces the deprecated @app.on_event('startup')."""
    from database.sql_db import default_sql_db
    env = os.environ.get("ENVIRONMENT", "production")
    demo_mode = os.getenv("VHV_DEMO_MODE", "false").lower() == "true"
    if env == "production" and demo_mode:
        # Demo mode seeds default accounts whose passwords are published in the
        # README — catastrophic on a production deployment. Refuse to seed and
        # shout, rather than silently standing up admin/Admin@2026Secure!.
        logger.critical(
            "VHV_DEMO_MODE=true with ENVIRONMENT=production — REFUSING to seed the "
            "default demo accounts (their passwords are public). Unset VHV_DEMO_MODE "
            "in production."
        )
        demo_mode = False
    if env == "development" or demo_mode:
        storage.seed_default_users()
        default_sql_db.seed_default_users()
        logger.info("Seeding default users (Development/Demo Mode)")
        # A first run should show the vault working, not eight empty panels.
        try:
            from backend.demo_seed import seed_demo_chart_if_enabled
            if seed_demo_chart_if_enabled():
                logger.info("Demo client file ready (CL-001)")
        except Exception as e:
            logger.warning(f"Demo chart seeding skipped: {e}")
    else:
        logger.info("Production Mode — Skipping default user seeding")
    logger.info(f"PsikoNot API v5.0.0 ready - Device: {get_device_id()[:16]}...")
    yield


app = FastAPI(
    title="PsikoNot API",
    version="5.0.0",
    description="Encrypted, tamper-evident client records for independent psychologists",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)

# CORS Middleware
origins = ["http://localhost:8090", "http://127.0.0.1:8090", "http://localhost:8000", "http://127.0.0.1:8000"]
vhv_origins = os.environ.get("VHV_ALLOWED_ORIGINS")
if vhv_origins:
    for item in vhv_origins.split(","):
        item = item.strip()
        if item and item not in origins:
            origins.append(item)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
)

# Custom Middlewares
app.add_middleware(IPAllowlistMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(RateLimiterMiddleware)
app.add_middleware(XSSProtectionMiddleware)

# Register routers
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(records_router)
app.include_router(misc_router)
app.include_router(alerts_router)

# Phase 2 — Pseudonymization (Identity Decoupling) Router
from backend.routers.pseudonymization import router as pseudonym_router
app.include_router(pseudonym_router)

# Out-of-band account provisioning & enrollment
from backend.routers.onboarding import router as onboarding_router
app.include_router(onboarding_router)

# The client list: cards with contact details and the visit tally
from backend.routers.clients import router as clients_router
app.include_router(clients_router)

# The appointment book (practitioners and their secretaries)
from backend.routers.appointments import router as appointments_router
app.include_router(appointments_router)

# KVKK: a client's data export and erasure requests
from backend.routers.kvkk import router as kvkk_router
app.include_router(kvkk_router)

# GDPR/KVKK Art. 17 — crypto-shredding erasure
from backend.routers.erasure import router as erasure_router
app.include_router(erasure_router)


@app.get("/api/v1/health", summary="System Health Metrics")
def health_check():
    """
    Liveness probe for container orchestration.

    Deliberately anonymous: it is the one route the IP allowlist lets through, so
    it must not disclose the device fingerprint or anything else identifying.
    """
    from database.sql_db import default_sql_db

    health_status = {
        "status": "healthy",
        "database": "unknown",
        "blockchain_simulation": os.getenv("VHV_DEMO_MODE", "false").lower() == "true",
    }

    try:
        conn = default_sql_db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        health_status["database"] = "connected (PostgreSQL)" if default_sql_db.is_postgres else "connected (SQLite)"
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["database"] = f"error: {str(e)}"

    return health_status

# Serve static frontend SPA files
STATIC = os.path.join(os.path.dirname(__file__), "static")

if os.path.isdir(STATIC):
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def revalidate_frontend_assets(request, call_next):
        """
        Force browsers to revalidate the SPA shell and its ES modules.

        Without this a browser keeps serving heuristically-cached JavaScript after
        a deployment, so frontend security fixes silently fail to reach anyone who
        already visited the vault.
        """
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    @app.get("/", include_in_schema=False)
    async def frontend_root():
        return FileResponse(os.path.join(STATIC, "index.html"))

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend_spa(path: str = ""):
        # Skip API paths
        if path.startswith("api/"):
            raise HTTPException(404)
        return FileResponse(os.path.join(STATIC, "index.html"))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8090))
    reload_mode = os.environ.get("ENVIRONMENT", "development") == "development"
    # Loopback by default. This vault is meant to sit inside a private subnet
    # (see IPAllowlistMiddleware and PRIVATE_VPC_DEPLOYMENT.md); binding every
    # interface has to be a deliberate act, not the default it used to be.
    host = os.environ.get("VHV_BIND_HOST", "127.0.0.1")
    uvicorn.run("main:app", host=host, port=port, reload=reload_mode)

