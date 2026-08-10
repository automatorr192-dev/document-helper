import pytest

import share

REPORT = {
    "verdict": "5 ловушек, 2 критичные",
    "summary": "Договор оказания услуг с невозвратной предоплатой.",
    "findings": [
        {
            "severity": "red",
            "title": "Предоплата не возвращается",
            "quote": "внесённые средства не подлежат возврату",
            "plain": "Передумаешь — деньги останутся у исполнителя.",
            "article": "ст. 782 ГК РФ",
            "start": 120,
            "end": 168,
        }
    ],
    "actions": ["Убрать пункт о невозврате"],
}


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(share, "REPORTS", str(tmp_path / "reports"))


def test_saved_report_reads_back():
    token = share.save(REPORT)
    loaded = share.load(token)
    assert loaded["verdict"] == REPORT["verdict"]
    assert loaded["findings"][0]["quote"] == REPORT["findings"][0]["quote"]


def test_contract_coordinates_are_not_stored():
    """Храним отчёт, а не договор: координаты цитат нужны только внутри мини-аппа."""
    loaded = share.load(share.save(REPORT))
    assert "start" not in loaded["findings"][0]
    assert "end" not in loaded["findings"][0]


def test_tokens_are_unique():
    assert share.save(REPORT) != share.save(REPORT)


def test_unknown_token_is_not_found():
    assert share.load("нет-такого") is None


def test_token_cannot_escape_the_folder():
    """Токен приходит из адреса и не должен превращаться в путь."""
    assert share.load("../../.env") is None
    assert share.load("..%2F..%2Fetc") is None


def test_expired_link_stops_working(monkeypatch):
    token = share.save(REPORT)
    monkeypatch.setattr(share, "TTL", -1)
    assert share.load(token) is None


def test_purge_removes_expired_files(monkeypatch):
    share.save(REPORT)
    monkeypatch.setattr(share, "TTL", -1)
    assert share.purge() == 1


def test_html_escapes_markup_from_the_contract():
    """Внутри PDF может лежать разметка, и в цитату она попадёт как есть."""
    evil = dict(REPORT)
    evil["findings"] = [
        {
            "severity": "red",
            "title": "<script>alert(1)</script>",
            "quote": "<img src=x onerror=alert(2)>",
            "plain": "текст",
            "article": None,
        }
    ]
    page = share.render_html(evil)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "onerror=alert(2)>" not in page


def test_html_has_every_finding():
    page = share.render_html(REPORT)
    assert REPORT["findings"][0]["title"] in page
    assert "Убрать пункт о невозврате" in page
