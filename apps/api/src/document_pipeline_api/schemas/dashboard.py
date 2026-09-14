from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CardInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    table_id: str = Field(min_length=1, max_length=128)
    metric: Literal["count", "sum", "avg"] = "count"
    metric_field: str | None = Field(default=None, max_length=128)
    group_field: str | None = Field(default=None, max_length=128)
    time_bucket: Literal["day", "month"] | None = None
    date_field: str | None = Field(default=None, max_length=128)
    time_range: Literal["dashboard", "all"] = "all"
    display: Literal["auto", "number", "line", "bar", "donut"] = "auto"

    @model_validator(mode="after")
    def compatible(self):
        if self.metric != "count" and not self.metric_field:
            raise ValueError("合计或平均值需要选择数值字段。")
        if self.metric == "count" and self.metric_field:
            raise ValueError("记录数无需指定数值字段。")
        if self.time_bucket and not self.group_field:
            raise ValueError("按日或月分组需要日期字段。")
        if self.time_range == "dashboard" and not self.date_field:
            raise ValueError("跟随仪表盘范围需要选择日期字段。")
        if self.display == "line" and not self.time_bucket:
            raise ValueError("趋势图需要按日期分组。")
        if self.display == "donut" and (not self.group_field or self.metric == "avg"):
            raise ValueError("环图需要可相加的分类统计；平均值请用条形图。")
        if self.display in {"bar", "line"} and not self.group_field:
            raise ValueError("图表需要选择分组字段。")
        return self


class CardUpdate(CardInput):
    expected_updated_at: datetime


class CardOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(max_length=4)
