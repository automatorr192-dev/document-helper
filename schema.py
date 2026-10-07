from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["red", "yellow", "green"]


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


class Finding(BaseModel):
    quote: str
    severity: Severity
    title: str
    plain: str
    article: str | None = None
    fix: str | None = None
    basis: str | None = None
    start: int = -1
    end: int = -1


class Findings(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


class Report(BaseModel):
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)

    @property
    def traps(self) -> int:
        return sum(1 for f in self.findings if f.severity != "green")

    @property
    def critical(self) -> int:
        return sum(1 for f in self.findings if f.severity == "red")

    @property
    def verdict(self) -> str:
        if not self.traps:
            return "Ловушек не нашёл"
        head = f"{self.traps} {plural(self.traps, 'ловушка', 'ловушки', 'ловушек')}"
        if not self.critical:
            return head
        word = plural(self.critical, "критичная", "критичные", "критичных")
        return f"{head}, {self.critical} {word}"


class DocField(BaseModel):
    key: str
    value: str | None = None
    quote: str | None = None
    start: int = -1
    end: int = -1


class Card(BaseModel):
    fields: list[DocField] = Field(default_factory=list)

    @property
    def filled(self) -> list[DocField]:
        return [f for f in self.fields if f.value]

    @property
    def confirmed(self) -> int:
        return sum(1 for f in self.filled if f.start >= 0)
