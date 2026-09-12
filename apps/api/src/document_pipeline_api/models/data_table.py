from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from document_pipeline_api.db import Base
from document_pipeline_api.models.task import utc_now


class DataTableRecord(Base):
    __tablename__ = "data_tables"
    __table_args__ = (
        UniqueConstraint(
            "template_key",
            "template_version",
            name="uq_data_table_template_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    template_key: Mapped[str] = mapped_column(String(128))
    template_version: Mapped[str] = mapped_column(String(64))
    document_kind: Mapped[str] = mapped_column(String(32))
    # 手动新建/合并表的列定义（[{"key","label","value_type"}]）；内置与模板表留空，
    # 列契约从内置映射或模板字段推导。仅用于无模板驱动的表。
    columns_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    presentation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConfirmedDocumentRecord(Base):
    __tablename__ = "confirmed_documents"

    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    table_id: Mapped[str] = mapped_column(
        ForeignKey("data_tables.id"),
        index=True,
    )
    review_version: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[str] = mapped_column(Text)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DataRowRecord(Base):
    __tablename__ = "data_rows"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "item_index",
            name="uq_data_row_task_item",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_id: Mapped[str] = mapped_column(
        ForeignKey("data_tables.id"),
        index=True,
    )
    # 手动新增的行没有来源任务；task_id 可空（SQLite UNIQUE 中 NULL 互不冲突）
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id"),
        index=True,
        nullable=True,
    )
    item_index: Mapped[int] = mapped_column(Integer)
    row_json: Mapped[str] = mapped_column(Text)
    input_scope_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_pending: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DataRowRevisionRecord(Base):
    __tablename__ = "data_row_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    row_id: Mapped[int] = mapped_column(Integer, index=True)
    table_id: Mapped[str] = mapped_column(String(36), index=True)
    # 手动行的修订没有来源任务，可空
    task_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(32))
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    editor: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DataViewRecord(Base):
    __tablename__ = "data_views"
    __table_args__ = (
        UniqueConstraint(
            "table_id",
            "field_key",
            "field_value_json",
            name="uq_data_view_filter",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    table_id: Mapped[str] = mapped_column(
        ForeignKey("data_tables.id"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128))
    field_key: Mapped[str] = mapped_column(String(128))
    field_value_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
