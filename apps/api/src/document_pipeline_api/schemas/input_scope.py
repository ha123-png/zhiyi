"""Native source coordinates; model coverage is separate from extraction accuracy."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InputPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    allow_limited_input: bool = False
    row_limit: int = Field(default=500, ge=1)
    docx_image_limit: int = Field(default=10, ge=1)
    accept_partial: bool = False
    include_images: bool = False
    text_budget: int = Field(default=2048, ge=1)
    image_budget: int = Field(default=10, ge=1)


class SourceRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["page", "frame", "line", "paragraph", "table_row", "sheet_row", "image", "document_part"]
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    container: str | None = None
    character_start: int | None = Field(default=None, ge=1)
    character_end: int | None = Field(default=None, ge=1)
    columns: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.end < self.start:
            raise ValueError("范围终点不能早于起点。")
        return self

    def label(self) -> str:
        span = str(self.start) if self.start == self.end else f"{self.start}–{self.end}"
        if self.kind == "sheet_row":
            from openpyxl.utils import get_column_letter

            return f"{self.container}!A{self.start}:{get_column_letter(self.columns or 1)}{self.end}"
        names = {"page": "页", "frame": "帧", "line": "行", "paragraph": "段落",
                 "table_row": "行", "image": "图片", "document_part": "部分"}
        prefix = f"{self.container} · " if self.container else ""
        return f"{prefix}第 {span} {names[self.kind]}" + (f" · 字符 {self.character_start}–{self.character_end}" if self.character_start is not None else "")


class OmittedRange(BaseModel):
    location: SourceRange
    reason: Literal["input_budget", "images_disabled", "unsupported_image", "unsupported_structure"] = "input_budget"


class InputScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    rule: Literal["all", "head_tail_v1", "prefix_v1"]
    coverage: Literal["complete", "partial"]
    selected: list[SourceRange]
    omitted: list[OmittedRange]
    selected_units: int = Field(ge=0)
    total_units: int = Field(ge=0)
    text_characters: int = Field(ge=0)
    text_budget: int = Field(ge=0)
    image_budget: int = Field(ge=0)
    notes: list[str] = Field(default_factory=list)

    def prompt_notice(self) -> str:
        selected = "；".join(location.label() for location in self.selected)
        if self.coverage == "complete":
            return f"本次提供的原始范围：{selected}。输入覆盖不保证提取正确。"
        return (
            f"这是局部读取，实际提供：{selected}。未列出的范围没有提供，边界内容可能不完整。"
            "未提供的内容不能推测；不要拼接跨缺口的题目、句子或记录。"
            "仅提取当前可见且含义完整的信息，不能声称全文摘要或全部明细。"
        )
