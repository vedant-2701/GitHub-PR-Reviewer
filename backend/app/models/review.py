"""
Review ORM model.

Created when the webhook fires (before LLM runs), so verdict/confidence/summary
are nullable — they are populated once the Celery task completes.

verdict is stored as a PostgreSQL native Enum. If Verdict values ever change,
Alembic will need an ALTER TYPE migration. This is intentional — DB-level
enforcement is worth the migration cost.
"""
import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class VerdictEnum(str, enum.Enum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT = "COMMENT"


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    repo: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    # Populated after LLM completes — nullable until then
    verdict: Mapped[str | None] = mapped_column(
        Enum(VerdictEnum, name="verdict_enum", create_constraint=True),
        nullable=True,
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    filtered_issues: Mapped[list["FilteredIssue"]] = relationship(  # noqa: F821
        "FilteredIssue",
        back_populates="review",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Review id={self.id} repo={self.repo} pr={self.pr_number} verdict={self.verdict}>"