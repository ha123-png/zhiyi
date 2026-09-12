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


class TemplateRecord(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    builtin_key: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        nullable=True,
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 智能匹配预选池：只有池内的模板才会参与智能匹配候选
    in_smart_pool: Mapped[bool] = mapped_column(Boolean, default=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    source_template_id: Mapped[str | None] = mapped_column(
        ForeignKey("templates.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class TemplateVersionRecord(Base):
    __tablename__ = "template_versions"
    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "version",
            name="uq_template_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[str] = mapped_column(
        ForeignKey("templates.id"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(512), default="")
    extra_instructions: Mapped[str] = mapped_column(Text, default="")
    fields_json: Mapped[str] = mapped_column(Text)
    validation_rules_json: Mapped[str] = mapped_column(Text, default="[]")
    deterministic_rules_json: Mapped[str] = mapped_column(Text, default="[]")
    output_mapping_json: Mapped[str] = mapped_column(Text, default="{}")
    behavior_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TemplateRestorationRecord(Base):
    __tablename__ = "template_restorations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id"), index=True)
    from_version: Mapped[int] = mapped_column(Integer)
    to_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
