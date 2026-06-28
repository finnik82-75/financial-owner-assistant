from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.routes import web, api

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Load and validate knowledge base on startup
    try:
        from app.services.knowledge_base_service import load_knowledge_base
        from app.services.knowledge_base_validator import validate_knowledge_base

        kb = load_knowledge_base()
        kb_status = validate_knowledge_base(kb)
        application.state.knowledge_base = kb
        application.state.kb_status = kb_status

        if kb_status["status"] == "success":
            print("[KB] Knowledge base loaded successfully.")
        else:
            print(f"[KB] Knowledge base loaded with {len(kb_status['errors'])} error(s):")
            for err in kb_status["errors"]:
                print(f"  - {err}")
    except Exception as exc:
        print(f"[KB] Failed to load knowledge base: {exc}")
        application.state.knowledge_base = {}
        application.state.kb_status = {
            "status": "error",
            "errors": [str(exc)],
            "warnings": [],
        }

    yield


app = FastAPI(
    title="Financial Owner Assistant",
    description="ИИ-ассистент анализа управленческой отчётности",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

app.include_router(web.router)
app.include_router(api.router, prefix="/api")
