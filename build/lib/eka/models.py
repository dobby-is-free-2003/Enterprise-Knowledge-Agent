from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Document:
    file_id: str
    title: str
    text: str
    owner: str
    allowed_principals: list[str]
    url: str = ""
    mime_type: str = "text/markdown"
    modified_at: str = ""

    def acl_signature(self) -> str:
        return ",".join(sorted(self.allowed_principals))


@dataclass
class Chunk:
    chunk_id: str
    file_id: str
    title: str
    url: str
    text: str
    chunk_index: int
    heading_path: str
    owner: str
    allowed_principals: list[str]
    mime_type: str
    modified_at: str
    content_hash: str

    def embed_text(self) -> str:
        return f"{self.title} | {self.heading_path}\n{self.text}"

    def to_payload(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_payload(cls, p: dict) -> "Chunk":
        return cls(**{k: p[k] for k in cls.__dataclass_fields__})  # type: ignore[attr-defined]


@dataclass
class Passage:
    label: str  # P1, P2, ... shown to the model
    chunk: Chunk
    score: float
    coverage: float


@dataclass
class UserCtx:
    """Identity attached by the API layer. The model never sees or controls this."""

    email: str
    display_name: str = ""
    groups: list[str] = field(default_factory=list)

    @property
    def principals(self) -> set[str]:
        return {f"user:{self.email}"} | {f"group:{g}" for g in self.groups}
