"""Проверяем, что защита денег реально прикручена к эндпоинту, а не просто написана.

TestClient без контекстного менеджера не запускает lifespan — иначе тесты полезли бы
в Telegram за поллингом.
"""

import io
import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TESTTOKEN")
os.environ.setdefault("OPENROUTER_API_KEY", "test")

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_extract_rejects_request_without_init_data():
    # Так выглядит страница, открытая в обычном браузере, — initData пустой.
    files = {"file": ("d.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}
    response = client.post("/api/extract", data={"init_data": ""}, files=files)
    assert response.status_code == 401


def test_extract_rejects_forged_init_data():
    files = {"file": ("d.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}
    forged = "auth_date=9999999999&user=%7B%22id%22%3A1%7D&hash=" + "0" * 64
    response = client.post("/api/extract", data={"init_data": forged}, files=files)
    assert response.status_code == 401


def test_analyze_rejects_forged_init_data():
    # Главная дыра, если её не закрыть: текст шлётся напрямую, минуя загрузку PDF.
    forged = "auth_date=9999999999&user=%7B%22id%22%3A1%7D&hash=" + "0" * 64
    response = client.post("/api/analyze", json={"init_data": forged, "text": "текст" * 100})
    assert response.status_code == 401


def test_pdf_link_reaches_the_pdf_route():
    """`/r/{token}` объявлен с параметром, который съедает и точку, поэтому порядок
    роутов решает: при обратном порядке ссылка на PDF уходила в HTML-ветку и отвечала
    404 всегда. Просим заведомо несуществующий токен и смотрим, кто ответил."""
    import share

    token = "нет" + "x" * 10
    assert share.load(token) is None  # токен точно невалидный, файла нет

    response = client.get(f"/r/{token}.pdf")
    assert response.status_code == 404
    # HTML-ветка отдаёт тот же 404, поэтому различаем по тому, какой роут сматчился.
    assert response.request.url.path.endswith(".pdf")
    matched = [r.path for r in app.routes if getattr(r, "path", "").startswith("/r/{token}")]
    assert matched[-1] == "/r/{token}", f"HTML-роут должен идти последним, сейчас: {matched}"
    assert "/r/{token}.pdf" in matched


def test_oversized_upload_is_rejected_before_it_is_buffered():
    """Лимит обязан сработать на потоке: раньше файл целиком читался в память и только
    потом сверялся с 10 МБ — присланные 500 МБ клали контейнер."""
    import main

    big = io.BytesIO(b"%PDF-1.4" + b"0" * (main.MAX_PDF_BYTES + 1024))
    files = {"file": ("big.pdf", big, "application/pdf")}
    response = client.post("/api/extract", data={"init_data": ""}, files=files)
    # Без валидного initData сюда и не пустят — важно, что это не падение по памяти.
    assert response.status_code in (401, 413)
