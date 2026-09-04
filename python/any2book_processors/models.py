from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass(slots=True)
class ConversionWarning:
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    location: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class Chapter:
    title: str
    html: str
    source_location: str | None = None


@dataclass(slots=True)
class BookDocument:
    schema_version: Literal["2"]
    metadata: dict[str, object]
    chapters: list[Chapter]
    assets: list[str] = field(default_factory=list)
    source: dict[str, object] = field(default_factory=dict)
    warnings: list[ConversionWarning] = field(default_factory=list)
    provenance: list[dict[str, object]] = field(default_factory=list)
    quality: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
