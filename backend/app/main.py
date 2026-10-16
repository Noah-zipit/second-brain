"""Second Brain API.

GET  /api/stats      pipeline counts
GET  /api/followups  items needing action
GET  /api/timeline   chronological pipeline events
POST /api/ask        {"question": "..."} -> grounded answer (+ learns back)
GET  /api/next       ?q=<name> -> what to do next and why (walks the graph)
GET  /api/nudge      full lead nudge queue with per-lead decisions
GET  /               dashboard UI
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.store import FalkorDBStore, JsonStore
from app.agent import Agent
from app.nudge import nudge_queue

ROOT = Path(__file__).resolve().parent.parent.parent
STORE_PATH = ROOT / "data" / "store.json"
UI_DIR = ROOT / "ui"

app = FastAPI(title="Second Brain", version="0.3.0")
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


def store():
    """FalkorDB is the live store (Day 2). JsonStore remains the durable
    source of truth for migrations and the offline fallback."""
    try:
        return FalkorDBStore(
            uri=os.environ.get("FALKORDB_URI") or None,
            host=os.environ.get("FALKORDB_HOST", "localhost"),
            port=int(os.environ.get("FALKORDB_PORT", "6379")),
        )
    except Exception as e:  # FalkorDB down -> keep the API serving from JSON
        print(f"[second-brain] FalkorDB unreachable ({e}); using JsonStore")
        return JsonStore(STORE_PATH)


def agent():
    return Agent(store())


class AskRequest(BaseModel):
    question: str


@app.get("/")
def index():
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/stats")
def stats():
    return store().stats()


@app.get("/api/followups")
def followups(limit: int = 20):
    return {"followups": store().get_followups(limit=limit)}


@app.get("/api/timeline")
def timeline(limit: int = 50):
    return {"timeline": store().timeline(limit=limit)}


@app.get("/api/search")
def search(q: str, limit: int = 20):
    return {"results": store().search(q, limit=limit)}


@app.post("/api/ask")
def ask(req: AskRequest):
    res = agent().ask(req.question)
    return {"question": req.question, **res}


@app.get("/api/next")
def next_step(q: str):
    """What should I do next about <q>, and why — walked from the graph."""
    return agent().what_next(q)


@app.get("/api/nudge")
def nudge():
    """Lead nudge queue: per-lead score/age/idle decisions from live graph."""
    return {"queue": nudge_queue(store())}
