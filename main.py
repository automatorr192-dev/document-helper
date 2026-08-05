import asyncio
import logging
import os
import tempfile
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import analyzer
import bot as tg
import tgauth
from extract import ScannedPdfError, pdf_text

logging.basicConfig(level=logging.INFO)

HERE = os.path.dirname(__file__)
WEBAPP = os.path.join(HERE, "webapp")
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 60_000
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", 5))


class AnalyzeIn(BaseModel):
    init_data: str = ""
    text: str = ""


_used: dict[int, list[float]] = defaultdict(list)


def _take_quota(user_id: int) -> int:
    """Разбор стоит ~8 ₽ живых денег, поэтому лимит на пользователя в сутки."""
    now = time.time()
    recent = [t for t in _used[user_id] if now - t < 24 * 3600]
    if len(recent) >= DAILY_LIMIT:
        _used[user_id] = recent
        raise HTTPException(429, f"На сегодня хватит — {DAILY_LIMIT} разборов в сутки.")
    recent.append(now)
    _used[user_id] = recent
    return DAILY_LIMIT - len(recent)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Бот живёт фоновой задачей внутри веб-приложения: в Amvera каждое приложение
    # тарифицируется отдельно, поэтому веб и бот делят один контейнер.
    task = asyncio.create_task(tg.main())
    yield
    task.cancel()


app = FastAPI(title="Договор-рентген", lifespan=lifespan)

# Статика может жить на GitHub Pages, а API — отдельно. Пускаем только свои адреса:
# API тратит деньги на каждый разбор, открывать его всему интернету незачем.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "https://automatorr192-dev.github.io").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _user(init_data: str) -> dict:
    try:
        return tgauth.check(init_data, os.environ["TELEGRAM_BOT_TOKEN"])
    except tgauth.BadInitData as e:
        raise HTTPException(401, "Открой рентген через бота — так я знаю, что это ты.") from e


@app.post("/api/extract")
async def api_extract(file: UploadFile = File(...), init_data: str = Form("")):
    """Быстрый шаг: только текст из PDF. Мини-апп показывает договор, пока идёт разбор."""
    _user(init_data)

    raw = await file.read()
    if len(raw) > MAX_PDF_BYTES:
        raise HTTPException(413, "Файл больше 10 МБ. Пришли договор одним PDF поменьше.")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(raw)
    tmp.close()
    try:
        text = pdf_text(tmp.name)
    except ScannedPdfError as e:
        raise HTTPException(422, "Похоже на скан — текста внутри нет. OCR пока не умею.") from e
    except Exception as e:
        raise HTTPException(422, "Не смог прочитать этот PDF.") from e
    finally:
        os.remove(tmp.name)

    return {"text": text[:MAX_TEXT_CHARS]}


@app.post("/api/analyze")
async def api_analyze(body: AnalyzeIn):
    user = _user(body.init_data)
    text = body.text.strip()
    if len(text) < 200:
        raise HTTPException(422, "Маловато текста для разбора.")

    left = _take_quota(user.get("id", 0))

    try:
        report, replies = await analyzer.analyze(text[:MAX_TEXT_CHARS])
    except RuntimeError as e:
        raise HTTPException(503, f"ИИ временно недоступен ({e}). Попробуй ещё раз.") from e

    cost = sum(r.cost for r in replies)
    logging.info("разбор для %s: %d находок, %.2f руб", user.get("id"), len(report.findings), cost)

    return JSONResponse(
        {
            "verdict": report.verdict,
            "summary": report.summary,
            "findings": [f.model_dump() for f in report.findings],
            "actions": report.actions,
            "left": left,
        }
    )


if os.path.isdir(WEBAPP):
    app.mount("/", StaticFiles(directory=WEBAPP, html=True), name="webapp")
