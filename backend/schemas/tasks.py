"""
backend/schemas/tasks.py — request bodies of the staff task list
================================================================
Fixed codes only, and no free-text field: the tasks table is not encrypted,
so nothing clinical may be typed into it. extra="forbid" refuses any field
these schemas do not name (a "title", a "note"...). The code lists live in
core/services/tasks.py.
"""

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
_ITEM_ID = re.compile(r"(?:CL|APP|APT)-[A-Z0-9]{3,20}")
_USERNAME = re.compile(r"[A-Za-z0-9._-]{3,50}")


def _code(v, allowed, what):
    if v is not None and v not in allowed:
        raise ValueError(f"Unknown {what}")
    return v


def _due_on(v):
    from datetime import datetime
    if v is None:
        return v
    if not isinstance(v, str) or not _DATE.fullmatch(v):
        raise ValueError("Use YYYY-MM-DD")
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Use YYYY-MM-DD")
    return v


def _due_time(v):
    if v is not None and (not isinstance(v, str) or not _TIME.fullmatch(v)):
        raise ValueError("Use HH:MM")
    return v


class TaskReq(BaseModel):
    """A new task: a type, the one item it is about (optional), who does it,
    when, and how urgent. Nothing else."""
    model_config = ConfigDict(extra="forbid")

    type: str
    link_type: Optional[str] = None
    link_id: Optional[str] = None
    assignee: Optional[str] = None
    due_on: str
    due_time: Optional[str] = None
    priority: str = "normal"

    @field_validator("type")
    @classmethod
    def v_type(cls, v):
        from core.services.tasks import TYPES
        return _code(v, TYPES, "task type")

    @field_validator("link_type")
    @classmethod
    def v_link_type(cls, v):
        from core.services.tasks import LINK_TYPES
        return _code(v, LINK_TYPES, "link type")

    @field_validator("link_id")
    @classmethod
    def v_link_id(cls, v):
        if v is not None and not _ITEM_ID.fullmatch(v):
            raise ValueError("Not a client, application or appointment ID")
        return v

    @field_validator("assignee")
    @classmethod
    def v_assignee(cls, v):
        if v is not None and not _USERNAME.fullmatch(v):
            raise ValueError("Not a username")
        return v

    @field_validator("due_on")
    @classmethod
    def v_due_on(cls, v):
        return _due_on(v)

    @field_validator("due_time")
    @classmethod
    def v_due_time(cls, v):
        return _due_time(v)

    @field_validator("priority")
    @classmethod
    def v_priority(cls, v):
        from core.services.tasks import PRIORITIES
        return _code(v, PRIORITIES, "priority")

    @model_validator(mode="after")
    def v_link_pair(self):
        sent = self.model_fields_set
        if ("link_type" in sent or "link_id" in sent) and (self.link_type is None) != (self.link_id is None):
            raise ValueError("Give both the kind and the ID of the linked item")
        return self


class TaskUpdateReq(TaskReq):
    """The same fields, all optional: only those sent are changed. A link is
    changed (or removed, with two nulls) as a pair."""
    type: Optional[str] = None
    due_on: Optional[str] = None
    priority: Optional[str] = None
