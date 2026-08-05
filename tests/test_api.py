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
