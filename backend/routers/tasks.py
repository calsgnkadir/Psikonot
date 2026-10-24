"""
backend/routers/tasks.py — the staff task list ("İşler")
=========================================================
The front desk's and practitioners' shared to-do list. The rules — fixed
codes, links, status, automatic tasks, retention — live in
core/services/tasks.py; this router decides whose tasks a request may touch:

  * a practitioner: the tasks in their own book;
  * a secretary: the tasks in every book they are linked to (read on every
    request: unlinking ends access at once). A request that creates a task, or
    reads the pickers for one, names the book when they run several
    (?practitioner=);
  * operators (admin, security officer, auditor): counts per status for the
    whole center, never a task — as they never see a client card.

A task someone may not see gets the same 404 as a missing one, and so does a
linked item from another book. The audit trail names tasks and their links by
ID only.
"""

import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import current_user
from backend.routers.appointments import book_owner, books_of
from backend.schemas.tasks import TaskReq, TaskUpdateReq
from core.services import appointment_book, client_registry, tasks
from core.services.access_policy import OPERATOR_ROLES
from infrastructure.repositories.sql_repositories import SQLUserRepository

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

STAFF_ROLES = ("secretary", "practitioner")
NOT_STAFF = "Only the front desk and practitioners work with tasks."
APPOINTMENT_WINDOW = (7, 60)    # days back and ahead offered in the link picker


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts is not None else None


def _staff(u: dict) -> dict:
    if u.get("role") not in STAFF_ROLES:
        raise HTTPException(403, NOT_STAFF)
    return u


def _refuse(e: tasks.TaskError):
    raise HTTPException(e.status, str(e))


def _load(u: dict, task_id: str) -> dict:
    task = tasks.get(task_id)
    if not tasks.can_see(u, task):
        raise HTTPException(404, tasks.NOT_FOUND)
    return task


class _Names:
    """Account names for one response, each looked up once."""

    def __init__(self):
        self._repo, self._seen = SQLUserRepository(), {}

    def user(self, username: Optional[str]) -> Optional[dict]:
        if not username:
            return None
        if username not in self._seen:
            account = self._repo.load_user(username)
            self._seen[username] = ({"full_name": account.full_name, "role": account.role} if account
                                    else {"full_name": username, "role": None})
        return self._seen[username]

    def __call__(self, username: Optional[str]) -> Optional[str]:
        entry = self.user(username)
        return entry["full_name"] if entry else None


def _view(t: dict, names: _Names) -> dict:
    link = tasks.link_info(t)
    if link:
        link = dict(link, starts_at=_iso(link["starts_at"]))
    return {
        "id": t["id"],
        "practitioner": t["practitioner_username"],
        "practitioner_name": names(t["practitioner_username"]),
        "type": t["type"],
        "link": link,
        "assignee": t["assignee"],
        "assignee_name": names(t["assignee"]),
        "due_on": t["due_on"],
        "due_time": t["due_time"],
        "priority": t["priority"],
        "status": t["status"],
        "overdue": tasks.is_overdue(t),
        "automatic": t["created_by"] == tasks.SYSTEM,
        "created_by": t["created_by"],
        "created_at": _iso(t["created_at"]),
        "updated_by": t["updated_by"],
        "updated_at": _iso(t["updated_at"]),
        "done_by": t["done_by"],
        "done_at": _iso(t["done_at"]),
    }


def _one(t: dict) -> dict:
    return {"task": _view(t, _Names())}


# ── Reading ───────────────────────────────────────────────────
@router.get("", summary="My tasks: today, upcoming or done")
def list_tasks(
    view: str = "today",
    assignee: Optional[str] = None,
    type: Optional[str] = None,
    practitioner: Optional[str] = None,
    u: dict = Depends(current_user),
):
    """today: open and due today or earlier (overdue first); upcoming: open
    and due later; done: done or cancelled, the latest first. `assignee` is a
    username, "me" or "unassigned"; `practitioner` narrows to one book."""
    _staff(u)
    if view not in tasks.VIEWS:
        raise HTTPException(422, "Unknown view")
    if type is not None and type not in tasks.TYPES:
        raise HTTPException(422, "Unknown task type")
    books = [book_owner(u, practitioner)] if practitioner is not None else books_of(u)
    tasks.maybe_run()
    who = u["username"] if assignee == "me" else assignee
    items = tasks.list_for(books, view=view, assignee=None if assignee == "unassigned" else who, kind=type,
                           unassigned=assignee == "unassigned")
    names = _Names()
    return {"tasks": [_view(t, names) for t in items], "today": tasks.today().isoformat(),
            "retention_days": tasks.retention_days()}


@router.get("/summary", summary="How many tasks are due")
def summary(u: dict = Depends(current_user)):
    """Staff: their books' open tasks due today or earlier, overdue, and
    upcoming (the menu badge and the dashboard card). Operators: the whole
    center per status, as numbers only."""
    if u.get("role") in OPERATOR_ROLES:
        return {"counts": tasks.center_counts()}
    _staff(u)
    books = appointment_book.books_for(u)
    if books:
        tasks.maybe_run()
    return {"counts": tasks.counts_for(books)}


@router.get("/options", summary="What a task in one book may link to and who may do it")
def options(practitioner: Optional[str] = None, u: dict = Depends(current_user)):
    _staff(u)
    book = book_owner(u, practitioner)
    now = time.time()
    back, ahead = APPOINTMENT_WINDOW
    names = {c["patient_id"]: c["full_name"] for c in client_registry.list_for(book)}
    appointments = [a for a in appointment_book.list_appointments(practitioner=book, start=now - back * 86400,
                                                                  end=now + ahead * 86400)
                    if a["status"] == "scheduled"]
    applications = []
    for a in tasks.open_applications_of(book):
        label = a["full_name"] or names.get(a["client_id"])
        applications.append({"id": a["id"], "label": label, "received_at": _iso(a["received_at"]),
                             "status": a["status"]})
    lookup = _Names()
    people = [book] + appointment_book.staff_of(book)
    return {
        "practitioner": book,
        "clients": [{"id": pid, "label": name} for pid, name in names.items()],
        "applications": applications,
        "appointments": [{"id": a["id"], "label": names.get(a["patient_id"], a["patient_id"]),
                          "starts_at": _iso(a["starts_at"])} for a in appointments],
        "assignees": [{"username": p, "full_name": lookup(p), "role": (lookup.user(p) or {}).get("role")}
                      for p in people],
    }


@router.get("/{task_id}", summary="One task")
def get_task(task_id: str, u: dict = Depends(current_user)):
    _staff(u)
    return _one(_load(u, task_id))


# ── Writing ───────────────────────────────────────────────────
@router.post("", summary="Add a task")
def create_task(req: TaskReq, practitioner: Optional[str] = None, u: dict = Depends(current_user)):
    _staff(u)
    book = book_owner(u, practitioner)
    try:
        task = tasks.create(book, req.model_dump(), by=u["username"])
    except tasks.TaskError as e:
        _refuse(e)
    tasks.audit("TASK_CREATED", u["username"], task, assignee=task["assignee"])
    return _one(task)


@router.patch("/{task_id}", summary="Change a task")
def update_task(task_id: str, req: TaskUpdateReq, u: dict = Depends(current_user)):
    _staff(u)
    task = _load(u, task_id)
    changes = req.model_dump(exclude_unset=True)
    for field in ("type", "due_on", "priority"):
        if field in changes and changes[field] is None:
            raise HTTPException(422, "This field cannot be empty")
    try:
        task = tasks.update(task, changes, by=u["username"])
    except tasks.TaskError as e:
        _refuse(e)
    tasks.audit("TASK_UPDATED", u["username"], task, fields=sorted(changes), assignee=task["assignee"])
    return _one(task)


def _move(u: dict, task_id: str, status: str, action: str) -> dict:
    _staff(u)
    task = _load(u, task_id)
    try:
        task = tasks.set_status(task, status, by=u["username"])
    except tasks.TaskError as e:
        _refuse(e)
    tasks.audit(action, u["username"], task)
    return _one(task)


@router.post("/{task_id}/done", summary="Mark a task done")
def mark_done(task_id: str, u: dict = Depends(current_user)):
    return _move(u, task_id, "done", "TASK_DONE")


@router.post("/{task_id}/cancel", summary="Cancel a task")
def cancel(task_id: str, u: dict = Depends(current_user)):
    return _move(u, task_id, "cancelled", "TASK_CANCELLED")


@router.post("/{task_id}/reopen", summary="Open a done or cancelled task again")
def reopen(task_id: str, u: dict = Depends(current_user)):
    return _move(u, task_id, "open", "TASK_REOPENED")
