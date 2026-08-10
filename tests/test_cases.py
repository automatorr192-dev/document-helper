"""Проверка самого датасета, а не продукта.

Разметка — это фрагменты, скопированные из договора руками. Опечатка в таком фрагменте
не роняет ничего: эвал просто честно показывает «не нашли» и выглядит как деградация
продукта. Ищется она потом часами, поэтому ищем её здесь и бесплатно.
"""

import json
from pathlib import Path

import pytest

from extract import pdf_text

ROOT = Path(__file__).resolve().parent.parent
_LINES = (ROOT / "evals" / "cases.jsonl").read_text("utf-8").splitlines()
CASES = [json.loads(line) for line in _LINES if line.strip()]


def flat(s: str) -> str:
    return " ".join(s.split()).lower()


def test_dataset_is_not_empty():
    assert len(CASES) >= 5


def test_ids_are_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case_shape(case):
    assert case["input"].get("pdf") or case["input"].get("text")
    assert "must_find" in case["expected"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_marked_fragments_exist_in_source(case):
    """Каждый размеченный фрагмент обязан встречаться в исходном документе дословно."""
    if pdf := case["input"].get("pdf"):
        path = ROOT / pdf
        if not path.exists():
            pytest.skip("датасет договоров лежит вне git")
        source = pdf_text(str(path))
    else:
        source = case["input"]["text"]

    haystack = flat(source)
    for fragment in case["expected"]["must_find"] + case["expected"].get("must_not_find", []):
        assert flat(fragment) in haystack, f"нет в документе: {fragment}"
