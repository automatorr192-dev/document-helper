"""Документ-хелпер в MAX: прислал договор файлом или текстом — получил разбор.

Ядро общее с Telegram: те же извлечение текста, распознавание сканов, разбор и лимит в
сутки. Протокол разногласий в Word отдаётся ссылкой на /r/<токен>.docx — той же
страницей «поделиться», что и в мини-аппе, — поэтому файл в MAX загружать не нужно.
Пользователи MAX считаются в лимите с отрицательным id: с телеграмными не пересекаются.
"""

import asyncio
import logging
import os
import ssl
import tempfile

import certifi
import httpx

import analyzer
import ocr
import protocol
import quota
import share
from bot import DAILY_LIMIT, MAX_DOC_BYTES, _split, render
from extract import ScannedPdfError, UnsupportedFormat, docx_text, kind, pdf_text, plain_text
from observability import log
from prompts import DISCLAIMER, UNAVAILABLE

API = "https://platform-api2.max.ru"
TOKEN = os.environ.get("MAX_BOT_TOKEN", "").strip()
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
CA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "certs", "russian_trusted_root_ca.pem"
)
TRANSPORT: httpx.AsyncBaseTransport | None = None
# Короче — это вопрос или «привет», а не договор: разбирать нечего, а разбор стоит денег.
MIN_CONTRACT_CHARS = 300

WELCOME = (
    "Пришлите договор файлом (PDF или Word) или вставьте текст — покажу пункты, которые "
    "могут стоить вам денег, и подскажу безопасную редакцию."
)


def enabled() -> bool:
    return bool(TOKEN)


def uid(user_id: int) -> int:
    return -abs(int(user_id))


def _trust() -> ssl.SSLContext:
    # API MAX работает на сертификате Минцифры: без этого корня httpx не соединится.
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(CA)
    return context


def client(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        verify=_trust() if TRANSPORT is None else True, transport=TRANSPORT, timeout=60, **kwargs
    )


async def send(http, chat_id: int, text: str) -> None:
    response = await http.post("/messages", params={"chat_id": chat_id}, json={"text": text[:4000]})
    if response.status_code != 200:
        log.warning("max.refused", status=response.status_code)


async def read_file(attachment: dict) -> str:
    """Текст из присланного файла. Скачиваем отдельным клиентом без токена бота: ссылка на
    файл ведёт на хранилище, и показывать ему токен незачем."""
    name = attachment.get("filename") or ""
    fmt = kind(name)
    if fmt == "doc":
        raise UnsupportedFormat("старый .doc — сохраните его как .docx или PDF")
    if (attachment.get("size") or 0) > MAX_DOC_BYTES:
        raise UnsupportedFormat("файл больше 10 МБ")
    async with client() as plain:
        response = await plain.get(attachment["payload"]["url"])
        response.raise_for_status()
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{fmt}") as tmp:
        tmp.write(response.content)
    try:
        if fmt == "docx":
            return await asyncio.to_thread(docx_text, tmp.name)
        if fmt == "txt":
            return await asyncio.to_thread(plain_text, tmp.name)
        try:
            return await asyncio.to_thread(pdf_text, tmp.name)
        except ScannedPdfError:
            text, _ = await ocr.text_from_scan(tmp.name)
            return text
    finally:
        os.remove(tmp.name)


async def analyze(http, chat_id: int, user_id: int, text: str) -> None:
    try:
        quota.take(uid(user_id), DAILY_LIMIT, "analyze")
    except quota.Exhausted as e:
        await send(
            http, chat_id, f"На сегодня хватит — {e.limit} разборов в сутки. Возвращайтесь завтра."
        )
        return
    await send(http, chat_id, "Читаю договор, это займёт около минуты…")
    try:
        report, replies = await analyzer.analyze(text)
    except RuntimeError as e:
        log.warning("llm.unavailable", error=str(e))
        await send(http, chat_id, UNAVAILABLE)
        return
    log.info(
        "analyze.done",
        source="max",
        findings=len(report.findings),
        cost_rub=round(sum(r.cost for r in replies), 2),
    )
    for part in _split(render(report)):
        await send(http, chat_id, part)
    data = report.model_dump() | {"verdict": report.verdict}
    if protocol.disputed(data) and PUBLIC_URL:
        token = await asyncio.to_thread(share.save, data)
        await send(
            http,
            chat_id,
            f"Протокол разногласий в Word: {PUBLIC_URL}/r/{token}.docx\n"
            f"Разбор целиком, чтобы переслать: {PUBLIC_URL}/r/{token}",
        )
    await send(http, chat_id, DISCLAIMER)


async def handle(http, update: dict) -> None:
    kind_ = update.get("update_type")
    if kind_ == "bot_started":
        await send(http, update["chat_id"], WELCOME)
        return
    if kind_ != "message_created":
        return
    message = update.get("message") or {}
    sender = message.get("sender") or {}
    chat_id = (message.get("recipient") or {}).get("chat_id")
    body = message.get("body") or {}
    files = [a for a in body.get("attachments") or [] if a.get("type") == "file"]
    text = (body.get("text") or "").strip()
    if files:
        try:
            text = await read_file(files[0])
        except UnsupportedFormat as e:
            await send(http, chat_id, f"Не смог прочитать файл: {e}.")
            return
        except ScannedPdfError:
            await send(http, chat_id, "На страницах не нашлось текста — разбирать нечего.")
            return
        except Exception:
            logging.exception("MAX: файл не прочитан")
            await send(http, chat_id, "Не смог прочитать файл. Пришлите PDF, Word или текст.")
            return
    elif text.startswith("/start") or len(text) < MIN_CONTRACT_CHARS:
        await send(http, chat_id, WELCOME)
        return
    await analyze(http, chat_id, sender.get("user_id", 0), text)


async def run() -> None:
    marker = None
    async with client(base_url=API, headers={"Authorization": TOKEN}) as http:
        while True:
            try:
                params: dict = {"timeout": 30, "types": "bot_started,message_created"}
                if marker is not None:
                    params["marker"] = marker
                response = await http.get("/updates", params=params)
                response.raise_for_status()
                data = response.json()
                marker = data.get("marker", marker)
                for update in data.get("updates", []):
                    try:
                        await handle(http, update)
                    except Exception:
                        log.exception("max.update_failed")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("max.polling_failed", retry_in=5)
                await asyncio.sleep(5)
