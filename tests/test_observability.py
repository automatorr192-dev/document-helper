import structlog

import observability


def fresh_setup(monkeypatch, *, pretty: bool, dsn: str = ""):
    monkeypatch.setenv("SENTRY_DSN", dsn)
    monkeypatch.setattr(observability, "_pretty", lambda: pretty)
    monkeypatch.setattr(observability, "_configured", False)
    observability.setup()
    return structlog.get_config()["processors"]


def has(processors, kind) -> bool:
    return any(isinstance(p, kind) for p in processors)


def test_setup_works_without_sentry(monkeypatch):
    """Пустой DSN — штатный режим разработки: ни сети, ни падения."""
    fresh_setup(monkeypatch, pretty=True)


def test_cloud_writes_json(monkeypatch):
    processors = fresh_setup(monkeypatch, pretty=False)
    assert has(processors, structlog.processors.JSONRenderer)


def test_terminal_writes_readable_lines(monkeypatch):
    processors = fresh_setup(monkeypatch, pretty=True)
    assert has(processors, structlog.dev.ConsoleRenderer)


def test_setup_runs_once(monkeypatch):
    """Бот и веб живут в одном процессе и оба зовут setup(). Второй вызов — пустышка."""
    fresh_setup(monkeypatch, pretty=True)

    monkeypatch.setattr(observability, "_pretty", lambda: False)
    observability.setup()

    assert has(structlog.get_config()["processors"], structlog.dev.ConsoleRenderer)
