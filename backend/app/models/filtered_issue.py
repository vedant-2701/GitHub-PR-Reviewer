"""
FilteredIssue ORM model.

Written once by guardrail.py after each guardrail_check() call.
Immutable — no updated_at, no updates ever.

review_id has CASCADE delete so filtered issues are cleaned up
if a review record is deleted.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FilteredIssue(Base):
    __tablename__ = "filtered_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    review_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issue_data: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-serialised Issue dict
    filter_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    file: Mapped[str] = mapped_column(String(500), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    review: Mapped["Review"] = relationship("Review", back_populates="filtered_issues")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<FilteredIssue id={self.id} review_id={self.review_id} "
            f"reason={self.filter_reason!r} file={self.file!r}>"
        )