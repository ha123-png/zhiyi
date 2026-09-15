from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LocalExportUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    mode: Literal["copy", "move"] = "copy"
    enabled: bool = False
    parent_path: str | None = Field(default=None, max_length=2048)


class LocalExportRead(BaseModel):
    revision: int = 0
    mode: Literal["copy", "move"] = "copy"
    enabled: bool = False
    parent_path: str | None = None
    destination: str | None = None


class TaskExportState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["copy", "move"] = "copy"
    source_removal_started: bool = False
    staging_path: str | None = None
    version: int = 1
    status: Literal["disabled", "awaiting_confirmation", "pending", "exporting", "completed", "failed", "skipped", "needs_rebind"] = "disabled"
    binding_revision: int = 0
    parent_path: str | None = None
    folder_name: str | None = None
    destination: str | None = None
    actual_path: str | None = None
    confirmed_name: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempted_path: str | None = None
    published_device: int | None = None
    published_inode: int | None = None


class ExportAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["retry", "skip"]
    filename: str | None = Field(default=None, max_length=512)
    parent_path: str | None = Field(default=None, max_length=2048)
    acknowledge_uncertain: bool = False
