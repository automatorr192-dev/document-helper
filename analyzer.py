import json

from pydantic import ValidationError

from llm import MODELS, Reply, chat
from prompts import ANALYZE_RETRY, ANALYZE_SYSTEM
from schema import Finding, Findings, Report


def _normalize(s: str) -> tuple[str, list[int]]:
    """Схлопывает пробелы и регистр, сохраняя карту в исходные индексы."""
    chars: list[str] = []
    index: list[int] = []
    prev_space = False
    for i, ch in enumerate(s):
        if ch.isspace():
            if prev_space or not chars:
                continue
            chars.append(" ")
            prev_space = True
        else:
            chars.append(ch.lower())
            prev_space = False
        index.append(i)
    return "".join(chars), index


def locate(text: str, quote: str) -> tuple[int, int] | None:
    """Границы цитаты в исходном тексте. Модель почти всегда врёт в пробелах."""
    pos = text.find(quote)
    if pos != -1:
        return pos, pos + len(quote)

    flat, index = _normalize(text)
    needle, _ = _normalize(quote)
    if not needle:
        return None
    pos = flat.find(needle)
    if pos == -1:
        return None
    return index[pos], index[pos + len(needle) - 1] + 1


def _json(raw: str) -> dict:
    return json.loads(raw[raw.index("{") : raw.rindex("}") + 1])


def _parse(raw: str) -> Report:
    return Report.model_validate(_json(raw))


def _anchor(findings: list[Finding], text: str) -> tuple[list[Finding], list[Finding]]:
    found, lost = [], []
    for f in findings:
        span = locate(text, f.quote)
        if span is None:
            lost.append(f)
        else:
            f.start, f.end = span
            found.append(f)
    return found, lost


async def analyze(text: str) -> tuple[Report, list[Reply]]:
    """Разбор договора. Возвращает отчёт с привязкой цитат к тексту и все ответы модели."""
    user = f"Текст договора:\n\n{text}"
    reply = await chat(ANALYZE_SYSTEM, user, MODELS, max_tokens=6000)
    replies = [reply]

    try:
        report = _parse(reply.text)
    except (ValueError, json.JSONDecodeError, ValidationError) as e:
        raise RuntimeError(f"модель вернула не JSON: {e}") from e

    found, lost = _anchor(report.findings, text)

    if lost:
        bad = "\n".join(f"- {f.quote}" for f in lost)
        retry = await chat(
            ANALYZE_SYSTEM,
            f"{user}\n\n{ANALYZE_RETRY.format(bad=bad)}",
            MODELS,
            max_tokens=2000,
        )
        replies.append(retry)
        try:
            second = Findings.model_validate(_json(retry.text))
        except (ValueError, json.JSONDecodeError, ValidationError):
            second = None
        if second is not None:
            found_again, _ = _anchor(second.findings, text)
            quotes = {f.quote for f in found}
            found += [f for f in found_again if f.quote not in quotes]

    report.findings = sorted(found, key=lambda f: f.start)
    return report, replies


async def analyze_image(data_url: str) -> tuple[Report, list[Reply]]:
    """Разбор по фото. Цитаты остаются без привязки — исходного текста у нас нет."""
    content = [
        {"type": "text", "text": "Разбери договор с этого фото."},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    reply = await chat(ANALYZE_SYSTEM, content, MODELS, max_tokens=6000)
    try:
        return _parse(reply.text), [reply]
    except (ValueError, json.JSONDecodeError, ValidationError) as e:
        raise RuntimeError(f"модель вернула не JSON: {e}") from e
