"""MAX: договор файлом или текстом → разбор, ссылки на протокол и отчёт, лимит в сутки.
Сеть подменена httpx.MockTransport, модель — готовым отчётом."""

import json

import httpx
import pytest

import maxbot
import quota
import share
from llm import Reply
from schema import Finding, Report

CONTRACT = "1. Предоплата 100% и не подлежит возврату ни при каких обстоятельствах. " * 6
REPORT = Report(
    summary="Договор услуг с невозвратной предоплатой.",
    findings=[
        Finding(
            quote="не подлежит возврату",
            severity="red",
            title="Предоплата не возвращается",
            plain="Передумаете — деньги останутся у исполнителя.",
            fix="Предоплата возвращается за вычетом фактических расходов.",
            start=24,
            end=44,
        )
    ],
    actions=["Убрать пункт о невозврате"],
)


@pytest.fixture
def max_api(tmp_path, monkeypatch):
    sent, files = [], {"https://files.max.ru/f/1": CONTRACT.encode("utf-8")}

    def handler(request: httpx.Request):
        if request.url.host == "files.max.ru":
            return httpx.Response(200, content=files[str(request.url)])
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(maxbot, "TRANSPORT", transport)
    monkeypatch.setattr(maxbot, "PUBLIC_URL", "https://helper.example")
    monkeypatch.setattr(share, "REPORTS", str(tmp_path / "reports"))
    monkeypatch.setattr(quota, "PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(quota, "DATA_DIR", str(tmp_path))
    analyzed = []

    async def fake_analyze(text):
        analyzed.append(text)
        return REPORT, [Reply(text="", model="m", input_tokens=1, output_tokens=1)]

    monkeypatch.setattr(maxbot.analyzer, "analyze", fake_analyze)
    http = httpx.AsyncClient(base_url=maxbot.API, transport=transport)
    http.sent, http.analyzed = sent, analyzed
    return http


def message(text="", attachments=None, user=55):
    return {
        "update_type": "message_created",
        "message": {
            "body": {"text": text, "attachments": attachments or []},
            "sender": {"user_id": user},
            "recipient": {"chat_id": 900},
        },
    }


async def test_file_is_read_analyzed_and_linked(max_api):
    attachment = {
        "type": "file",
        "filename": "договор.txt",
        "payload": {"url": "https://files.max.ru/f/1"},
    }

    await maxbot.handle(max_api, message(attachments=[attachment]))

    assert max_api.analyzed[0].startswith("1. Предоплата 100%")
    report = next(t for t in max_api.sent if "Предоплата не возвращается" in t)
    assert "«не подлежит возврату»" in report
    links = next(t for t in max_api.sent if "Протокол разногласий в Word" in t)
    assert "https://helper.example/r/" in links and ".docx" in links


async def test_pasted_contract_text_is_analyzed(max_api):
    await maxbot.handle(max_api, message(text=CONTRACT))

    assert len(max_api.analyzed) == 1


async def test_short_text_gets_instructions_not_a_paid_analysis(max_api):
    await maxbot.handle(max_api, message(text="Привет, что ты умеешь?"))

    assert max_api.analyzed == []
    assert max_api.sent == [maxbot.WELCOME]


async def test_old_doc_format_is_refused_before_download(max_api):
    attachment = {
        "type": "file",
        "filename": "договор.doc",
        "payload": {"url": "https://files.max.ru/f/2"},
    }

    await maxbot.handle(max_api, message(attachments=[attachment]))

    assert max_api.analyzed == []
    assert "docx" in max_api.sent[0]


async def test_daily_limit_counts_max_users_separately(max_api, monkeypatch):
    monkeypatch.setattr(maxbot, "DAILY_LIMIT", 1)

    await maxbot.handle(max_api, message(text=CONTRACT, user=55))
    await maxbot.handle(max_api, message(text=CONTRACT, user=55))
    await maxbot.handle(max_api, message(text=CONTRACT, user=56))

    assert len(max_api.analyzed) == 2
    assert any("На сегодня хватит" in t for t in max_api.sent)


async def test_model_failure_does_not_leak_details(max_api, monkeypatch):
    async def broken(text):
        raise RuntimeError("anthropic/claude-sonnet-5: Error code: 402 user_id=secret")

    monkeypatch.setattr(maxbot.analyzer, "analyze", broken)

    await maxbot.handle(max_api, message(text=CONTRACT))

    assert not any("secret" in t or "402" in t for t in max_api.sent)
    assert maxbot.UNAVAILABLE in max_api.sent
