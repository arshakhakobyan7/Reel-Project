"""Declarative base and column types shared by all Reel tables (D-027)."""

import json
from datetime import UTC, datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, MetaData
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

# Fixed constraint names, so later migrations can refer to them.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """The current time as an aware UTC datetime, the only kind Reel stores."""
    return datetime.now(UTC)


def json_dumps(value: Any) -> str:
    """Compact JSON that keeps Armenian text readable. NaN and infinity are rejected."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class UtcDateTime(TypeDecorator[datetime]):
    """Aware datetimes in, aware UTC datetimes out; stored as UTC text.

    Naive datetimes are rejected. Their time zone is unknown, and mixing them with UTC
    values would shift lease and retry times by the local UTC offset (+4 h in Yerevan).
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"expected a datetime, got {type(value).__name__}")
        if value.utcoffset() is None:
            raise ValueError("naive datetime: pass an aware datetime, e.g. reel.db.utcnow()")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


# JSON columns hold plain values. Assign a new value to change one: in-place changes such
# as ``job.flags.append(...)`` are not detected by the ORM and would not be saved.
JSON_VALUE = JSON(none_as_null=True)


def enum_type(enum_class: type[PyEnum], length: int = 32) -> Enum:
    """Store an enum by its value (e.g. ``"on_hold"``), validated in Python.

    There is no CHECK constraint in the database, so adding a value later needs no
    table rebuild (SQLite cannot alter constraints in place).
    """
    return Enum(
        enum_class,
        native_enum=False,
        create_constraint=False,
        length=length,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class Base(DeclarativeBase):
    """Base class of all Reel tables."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: UtcDateTime()}

    def __repr__(self) -> str:
        return f"<{type(self).__name__} id={getattr(self, 'id', None)}>"
