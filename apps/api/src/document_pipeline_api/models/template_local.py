"""Machine-local preferences; deliberately separate from shareable versions."""
from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from document_pipeline_api.db import Base


class TemplateLocalBindingRecord(Base):
    __tablename__ = "template_local_bindings"

    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), default="copy", server_default="copy")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    internal_folder: Mapped[str | None] = mapped_column(String(180), nullable=True, unique=True)
