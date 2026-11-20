"""웹 화면 서버 — uvicorn redline.server:app"""
from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from . import __version__
from .liner import Liner, LinerError
from .pipeline import list_packs, load_pack, review
from .report import to_markdown

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
MAX_CHARS = 12000


def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
app = FastAPI(title="Redline", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ReviewIn(BaseModel):
    text: str
    pack: str = "research"
    as_of: str | None = None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta():
    samples = []
    for p in sorted((ROOT / "samples").glob("*.md")):
        if p.stem.upper() == "README":
            continue
        t = p.read_text(encoding="utf-8")
        samples.append({"id": p.stem, "title": t.strip().splitlines()[0][:60], "text": t,
                        "pack": "proposal" if p.stem.startswith("proposal") else "research"})
    packs = []
    for p in list_packs():
        packs.append({**p, "rule_list": [{"id": r["id"], "name": r["name"], "check": r["check"], "origin": r.get("origin", "")}
                                         for r in load_pack(p["id"])["rules"]]})
    return {"version": __version__, "packs": packs, "samples": samples,
            "has_key": bool(os.environ.get("LINER_API_KEY")), "model": os.environ.get("REDLINE_MODEL", "liner-mark")}


@app.post("/api/review")
def api_review(body: ReviewIn):
    text = body.text.strip()
    if len(text) < 30:
        raise HTTPException(400, "원고가 너무 짧습니다.")
    if len(text) > MAX_CHARS:
        raise HTTPException(400, f"원고는 {MAX_CHARS:,}자까지 검토합니다.")
    try:
        liner = Liner()
    except LinerError as e:
        raise HTTPException(500, str(e))
    q: queue.Queue = queue.Queue()

    def work():
        try:
            review(text, body.pack, liner=liner, on_event=q.put, as_of=body.as_of or None)
        except Exception as e:  # noqa: BLE001 — 화면에 오류를 그대로 알린다
            q.put({"stage": "error", "msg": str(e)})
        q.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            ev = q.get()
            if ev is None:
                break
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/api/report", response_class=PlainTextResponse)
def api_report(result: dict):
    return to_markdown(result)
