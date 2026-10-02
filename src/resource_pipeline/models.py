from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .security import relative_path

FAMILIES = ("items", "tiles", "walls", "paints", "npcs", "buffs", "prefixes", "player", "worldgen", "markers", "pixel", "map", "locales", "ids")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(StrictModel):
    role: Literal["server", "client", "metadata"]
    game_version: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+){2,3}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Record(StrictModel):
    id: int | str
    name: dict[str, str] = Field(default_factory=dict)
    description: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list)
    aliases: list[int | str] = Field(default_factory=list)

    @model_validator(mode="after")
    def safe(self):
        for image in self.images:
            relative_path(image)
        if any(len(text) > 100_000 for text in (*self.name.values(), *self.description.values())):
            raise ValueError("Localized text too long")
        return self


class ExtractionEnvelope(StrictModel):
    schema_version: Literal[1]
    game_version: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+){2,3}$")
    adapter: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    source_kind: Literal["synthetic", "export-bundle"]
    sources: list[Source] = Field(min_length=1, max_length=10)
    families: dict[str, list[Record]]
    required_families: list[str] = Field(default_factory=lambda: list(FAMILIES))
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self):
        if any(source.game_version != self.game_version for source in self.sources):
            raise ValueError("Server/client/metadata version mismatch")
        if set(self.families) - set(FAMILIES) or set(self.required_families) - set(FAMILIES):
            raise ValueError("Unknown resource family")
        for family, records in self.families.items():
            ids = set()
            for record in records:
                identity = (type(record.id).__name__, record.id)
                if identity in ids:
                    raise ValueError(f"Duplicate ID in {family}")
                ids.add(identity)
            if len(records) > 100_000:
                raise ValueError("Too many records")
        return self
