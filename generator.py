import json
import os
import re

from playwright.async_api import async_playwright

from llm import MODELS, MODELS_LIGHT, chat
from prompts import BRIEF_CHECK_SYSTEM, GEN_SYSTEM, fenced

_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "document.html")

# Модель отдаёт HTML, который мы скармливаем настоящему браузеру. Значит бриф вида
# «добавь <script>…» становится выполняемым кодом внутри нашего контейнера. Промпта тут
# мало: оставляем только разметку из белого списка и вырезаем все атрибуты.
_ALLOWED_TAGS = {
    "h1",
    "h2",
    "h3",
    "p",
    "ol",
    "ul",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "strong",
    "em",
    "br",
    "hr",
}


def sanitize(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|iframe|object|embed)\b.*?</\1\s*>", "", html)

    def keep(m: re.Match) -> str:
        slash, name = m.group(1), m.group(2).lower()
        return f"<{slash}{name}>" if name in _ALLOWED_TAGS else ""

    return re.sub(r"(?is)<(/?)\s*([a-z0-9]+)\b[^>]*>", keep, html)


async def check_brief(doc_type: str, brief: str) -> tuple[bool, str]:
    reply = await chat(
        BRIEF_CHECK_SYSTEM,
        f"Тип документа: {doc_type}\nБриф пользователя:\n{fenced(brief)}",
        MODELS_LIGHT,
        max_tokens=200,
    )
    raw = reply.text
    try:
        data = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
        return bool(data.get("ok")), data.get("question", "")
    except (ValueError, json.JSONDecodeError):
        return True, ""


async def generate_body(doc_type: str, brief: str) -> str:
    reply = await chat(
        GEN_SYSTEM,
        f"Тип документа: {doc_type}\nБриф:\n{fenced(brief)}",
        MODELS,
        max_tokens=4000,
    )
    body = re.sub(r"^```(?:html)?|```$", "", reply.text.strip()).strip()
    return sanitize(body)


def _template() -> str:
    with open(_TEMPLATE, encoding="utf-8") as f:
        return f.read()


async def render_pdf(body_html: str, out_path: str) -> None:
    html = _template().replace("{{BODY}}", body_html)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # Скрипты выключены: в документе нечему исполняться, а вырваться наружу через
        # подсунутый в бриф код — нечему тем более.
        context = await browser.new_context(java_script_enabled=False)
        page = await context.new_page()
        await page.set_content(html, wait_until="load")
        await page.pdf(path=out_path, format="A4", print_background=True)
        await browser.close()
