# Import every model module here so that Base.metadata is fully populated
# before init_db() / Alembic calls create_all / autogenerate.
#
# Add a new import line every time a new model file is created.
#

"""
Import all ORM models here so Base.metadata has them registered
when init_db() calls create_all().

Order matters: Review must be imported before FilteredIssue
because FilteredIssue has a FK → reviews.id.
"""
from app.models.review import Review  # noqa: F401
from app.models.filtered_issue import FilteredIssue  # noqa: F401

__all__ = ["Review", "FilteredIssue"]
