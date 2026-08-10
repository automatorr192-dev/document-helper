import asyncio
import json
import os
import tempfile
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import analyzer
import bot as tg
import generator
import ocr
import quota
import share
import tgauth
from extract import ScannedPdfError, pdf_text
from observability import log, setup

setup()

HERE = os.path.dirname(__file__)
WEBAPP = os.path.join(HERE, "webapp")
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 60_000
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", 5))
# Парсинг PDF не платный, но нагружает процессор — свой лимит, пошире.
EXTRACT_LIMIT = int(os.environ.get("EXTRACT_LIMIT", 30))
SHARE_LIMIT = int(os.environ.get("SHARE_LIMIT", 20))
# Адрес, по которому приложение видно снаружи: из него собирается ссылка на отчёт.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")


class AnalyzeIn(BaseModel):
    init_data: str = ""
    text: str = ""


def _take(user_id: int, limit: int, bucket: str) -> int:
    try:
        return quota.take(user_id, limit, bucket)
    except quota.Exhausted as e:
        raise HTTPException(429, f"На сегодня хватит — {e.limit} разборов в сутки.") from e


async def _supervise_bot():
    """Бот делит процесс с веб-приложением, поэтому его падение веб не роняет — и наоборот,
    молча остаётся незамеченным. Логируем громко и поднимаем заново."""
    while True:
        try:
            await tg.main()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("bot.crashed", restart_in=5)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Бот живёт фоновой задачей внутри веб-приложения: в Amvera каждое приложение
    # тарифицируется отдельно, поэтому веб и бот делят один контейнер.
    task = asyncio.create_task(_supervise_bot())
    yield
    task.cancel()
    # Дожидаемся отмены: без этого aiogram не успевает закрыть сессию и оборвать polling.
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Документ-хелпер", lifespan=lifespan)

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
        detail = (
            "Мини-апп открыт слишком давно — закрой и открой заново."
            if "протухли" in str(e)
            else "Открой хелпер через бота — так я знаю, что это ты."
        )
        raise HTTPException(401, detail) from e


@app.post("/api/extract")
async def api_extract(file: UploadFile = File(...), init_data: str = Form("")):
    """Быстрый шаг: только текст из PDF. Мини-апп показывает договор, пока идёт разбор."""
    user = _user(init_data)
    _take(user.get("id", 0), EXTRACT_LIMIT, "extract")

    raw = await file.read()
    if len(raw) > MAX_PDF_BYTES:
        raise HTTPException(413, "Файл больше 10 МБ. Пришли договор одним PDF поменьше.")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(raw)
    tmp.close()
    scanned = False
    try:
        try:
            # pdfplumber синхронный и тяжёлый: в event loop он вешает и API, и бота в этом
            # же процессе. Считаем в отдельном потоке.
            text = await asyncio.to_thread(pdf_text, tmp.name)
        except ScannedPdfError:
            # Текста внутри нет — это скан. Распознавание платное, поэтому списывается из
            # той же корзины, что и разбор: у скана два платных шага вместо одного.
            _take(user.get("id", 0), DAILY_LIMIT, "analyze")
            text, _ = await ocr.text_from_scan(tmp.name)
            scanned = True
    except ScannedPdfError as e:
        raise HTTPException(422, "На страницах не нашлось текста — нечего разбирать.") from e
    except RuntimeError as e:
        raise HTTPException(503, f"Не смог распознать скан ({e}). Попробуй ещё раз.") from e
    except Exception as e:
        raise HTTPException(422, "Не смог прочитать этот PDF.") from e
    finally:
        os.remove(tmp.name)

    return {"text": text[:MAX_TEXT_CHARS], "scanned": scanned}


@app.post("/api/analyze")
async def api_analyze(body: AnalyzeIn):
    user = _user(body.init_data)
    text = body.text.strip()
    if len(text) < 200:
        raise HTTPException(422, "Маловато текста для разбора.")

    left = _take(user.get("id", 0), DAILY_LIMIT, "analyze")

    try:
        report, replies = await analyzer.analyze(text[:MAX_TEXT_CHARS])
    except RuntimeError as e:
        raise HTTPException(503, f"ИИ временно недоступен ({e}). Попробуй ещё раз.") from e

    cost = sum(r.cost for r in replies)
    log.info(
        "analyze.done",
        user_id=user.get("id"),
        findings=len(report.findings),
        cost_rub=round(cost, 2),
        chars=len(text),
        left=left,
    )

    return JSONResponse(
        {
            "verdict": report.verdict,
            "summary": report.summary,
            "findings": [f.model_dump() for f in report.findings],
            "actions": report.actions,
            "left": left,
        }
    )


def _sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/analyze/stream")
async def api_analyze_stream(body: AnalyzeIn):
    """То же, что /api/analyze, но находки уезжают по одной, как только модель их дописала.

    Ошибки после первого байта уже нельзя отдать кодом ответа — заголовки ушли. Поэтому
    всё, что может упасть до старта потока, проверяется здесь, а остальное превращается
    в событие error.
    """
    user = _user(body.init_data)
    text = body.text.strip()
    if len(text) < 200:
        raise HTTPException(422, "Маловато текста для разбора.")

    left = _take(user.get("id", 0), DAILY_LIMIT, "analyze")

    async def events():
        try:
            async for event in analyzer.analyze_stream(text[:MAX_TEXT_CHARS]):
                if event["type"] == "done":
                    event["data"]["left"] = left
                    log.info(
                        "analyze.done",
                        streaming=True,
                        user_id=user.get("id"),
                        findings=len(event["data"]["findings"]),
                        cost_rub=event["data"]["cost_rub"],
                        chars=len(text),
                    )
                yield _sse(event["type"], event["data"])
        except Exception as e:
            log.exception("analyze.stream_failed", user_id=user.get("id"))
            yield _sse("error", {"detail": f"ИИ временно недоступен ({e}). Попробуй ещё раз."})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # Без этого прокси буферизует ответ и складывает поток обратно в один кусок.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ShareIn(BaseModel):
    init_data: str = ""
    report: dict = {}


@app.post("/api/share")
async def api_share(body: ShareIn):
    """Сохраняет отчёт и отдаёт короткую ссылку. Сам договор не сохраняется."""
    user = _user(body.init_data)
    _take(user.get("id", 0), SHARE_LIMIT, "share")

    if not body.report.get("findings") and not body.report.get("summary"):
        raise HTTPException(422, "Нечего сохранять — отчёт пустой.")

    token = share.save(body.report)
    log.info("share.saved", user_id=user.get("id"), token=token)
    return {"url": f"{PUBLIC_URL}/r/{token}" if PUBLIC_URL else f"/r/{token}", "token": token}


@app.get("/r/{token}", response_class=HTMLResponse)
async def shared_report(token: str):
    report = share.load(token)
    if report is None:
        raise HTTPException(404, "Отчёт не найден или ссылка устарела.")
    return HTMLResponse(share.render_html(report))


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


@app.get("/r/{token}.pdf")
async def shared_report_pdf(token: str):
    report = share.load(token)
    if report is None:
        raise HTTPException(404, "Отчёт не найден или ссылка устарела.")

    out = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    out.close()
    try:
        await generator.html_to_pdf(share.render_html(report), out.name)
        pdf = await asyncio.to_thread(_read_bytes, out.name)
    finally:
        os.remove(out.name)

    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="razbor.pdf"'},
    )


class NoCacheStatic(StaticFiles):
    """Мини-апп кэшируется в WebView Telegram намертво, а способа сбросить этот кэш у
    Telegram нет. no-cache не запрещает кэшировать — он требует сверить ETag перед показом,
    поэтому новая версия доезжает сразу, а трафик почти не растёт."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


if os.path.isdir(WEBAPP):
    app.mount("/", NoCacheStatic(directory=WEBAPP, html=True), name="webapp")
