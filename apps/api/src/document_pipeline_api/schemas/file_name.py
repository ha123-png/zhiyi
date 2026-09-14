from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class FileNameDecision(BaseModel):
    before: str
    after: str
    confirmed_at: str


class FileNameRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    strategy: Literal["ai", "fixed"] = "ai"
    status: Literal["pending", "confirmed"] = "pending"
    suggested_filename: str
    confirmed_filename: str | None = None
    source_fields: list[str] = Field(default_factory=list)
    explanation: str
    decisions: list[FileNameDecision] = Field(default_factory=list)
