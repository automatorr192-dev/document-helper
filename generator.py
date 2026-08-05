import json
import os
import re

from playwright.async_api import async_playwright

from llm import MODELS, MODELS_LIGHT, chat
from prompts import BRIEF_CHECK_SYSTEM, GEN_SYSTEM

_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "document.html")


async def check_brief(doc_type: str, brief: str) -> tuple[bool, str]:
    reply = await chat(
        BRIEF_CHECK_SYSTEM,
        f"Тип документа: {doc_type}\nБриф пользователя: {brief}",
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
        f"Тип документа: {doc_type}\nБриф: {brief}",
        MODELS,
        max_tokens=4000,
    )
    return re.sub(r"^```(?:html)?|```$", "", reply.text.strip()).strip()


def _template() -> str:
    with open(_TEMPLATE, encoding="utf-8") as f:
        return f.read()


async def render_pdf(body_html: str, out_path: str) -> None:
    html = _template().replace("{{BODY}}", body_html)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        await page.pdf(path=out_path, format="A4", print_background=True)
        await browser.close()
