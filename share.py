"""Отчёт по короткой ссылке.

Продукт, результатом которого нельзя поделиться, дальше одного человека не уходит.
Поэтому разбор можно сохранить и отдать ссылкой — её открывают без Telegram и без входа.

Что важно: **сам договор мы не сохраняем**. В файл ложится только отчёт — цитаты опасных
пунктов и их разбор. Хранить чужой договор целиком ради кнопки «поделиться» — плохая
сделка: ценность маленькая, а утечка дорогая.

Ссылка живёт 30 дней. Токен случайный и достаточно длинный, чтобы его нельзя было
подобрать перебором: доступ к отчёту даёт знание ссылки, других замков тут нет.
"""

import html
import json
import os
import secrets
import string
import time

from prompts import DISCLAIMER

DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else "data")
REPORTS = os.path.join(DATA_DIR, "reports")
TTL = int(os.environ.get("SHARE_TTL_DAYS", 30)) * 24 * 3600

# Ровно то, что выдаёт secrets.token_urlsafe.
TOKEN_ALPHABET = frozenset(string.ascii_letters + string.digits + "-_")

KEEP = ("verdict", "summary", "findings", "actions")
FINDING_KEEP = ("severity", "title", "quote", "plain", "article", "fix", "basis")


def _path(token: str) -> str:
    return os.path.join(REPORTS, f"{token}.json")


def _slim(report: dict) -> dict:
    """Только то, что нужно показать. Координаты цитат и текст договора не сохраняем."""
    data = {k: report.get(k) for k in KEEP}
    data["findings"] = [{k: f.get(k) for k in FINDING_KEEP} for f in (report.get("findings") or [])]
    return data


def save(report: dict) -> str:
    os.makedirs(REPORTS, exist_ok=True)
    token = secrets.token_urlsafe(12)
    payload = _slim(report) | {"created_at": time.time()}
    with open(_path(token), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return token


def load(token: str) -> dict | None:
    # Токен приходит из адреса, поэтому в имя файла он не должен превратиться никогда.
    # Сверяем с алфавитом token_urlsafe напрямую: isalnum() пропускал кириллицу и прочий
    # юникод — на путь это не влияло, но проверка ловила не то, что должна.
    if not token or not set(token) <= TOKEN_ALPHABET:
        return None
    try:
        with open(_path(token), encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError):
        return None
    if time.time() - report.get("created_at", 0) > TTL:
        return None
    return report


_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "report.html")
LEVELS = {"red": "Опасно", "yellow": "Спорно", "green": "Нормально"}


def render_html(report: dict) -> str:
    """Страница отчёта. Одна и та же и для ссылки, и для PDF.

    Всё, что пришло от модели, экранируется: цитаты — это куски чужого документа, внутри
    которого может лежать разметка. В DOM она попадать не должна.
    """
    esc = html.escape

    items = []
    for f in report.get("findings") or []:
        severity = f.get("severity") if f.get("severity") in LEVELS else "yellow"
        article = f.get("article")
        fix = f.get("fix")
        items.append(
            f'<article class="item {severity}">'
            f'<span class="tag">{LEVELS[severity]}</span>'
            f"<h2>{esc(f.get('title') or '')}</h2>"
            f"<blockquote>{esc(f.get('quote') or '')}</blockquote>"
            f'<p class="plain">{esc(f.get("plain") or "")}</p>'
            + (f'<p class="article">{esc(article)}</p>' if article else "")
            + (f'<p class="plain"><b>Предложить вместо:</b> {esc(fix)}</p>' if fix else "")
            + "</article>"
        )

    steps = report.get("actions") or []
    actions = ""
    if steps:
        points = "".join(f"<li>{esc(s)}</li>" for s in steps)
        actions = f'<section class="actions"><h3>Перед подписанием</h3><ol>{points}</ol></section>'

    verdict = report.get("verdict") or "Разбор договора"
    summary = report.get("summary") or ""

    with open(_TEMPLATE, encoding="utf-8") as f:
        template = f.read()

    return (
        template.replace("{{TITLE}}", esc(verdict))
        .replace("{{VERDICT}}", esc(verdict))
        .replace("{{SUMMARY}}", esc(summary))
        .replace("{{ITEMS}}", "".join(items))
        .replace("{{ACTIONS}}", actions)
        .replace("{{DISCLAIMER}}", esc(DISCLAIMER))
    )


def purge() -> int:
    """Протухшие отчёты не должны копиться на диске вечно."""
    if not os.path.isdir(REPORTS):
        return 0
    removed = 0
    now = time.time()
    for name in os.listdir(REPORTS):
        path = os.path.join(REPORTS, name)
        try:
            if now - os.path.getmtime(path) > TTL:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed
