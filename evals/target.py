"""Обвязка над продуктом: зовём реальный analyze и приводим результат к общему виду.

Никакой логики здесь быть не должно. Если что-то чинишь ради эвалов тут, а не в
продукте — эвалы начинают мерить сами себя.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


async def run(case_input: dict) -> dict:
    from analyzer import analyze
    from extract import pdf_text

    text = case_input.get("text") or pdf_text(str(ROOT / case_input["pdf"]))
    report, replies = await analyze(text)

    return {
        "output": {
            "verdict": report.verdict,
            "anchored": all(f.start >= 0 for f in report.findings),
            "risks": [
                {"severity": f.severity, "title": f.title, "quote": f.quote}
                for f in report.findings
            ],
        },
        "usage": {
            "input_tokens": sum(r.input_tokens for r in replies),
            "output_tokens": sum(r.output_tokens for r in replies),
        },
    }
