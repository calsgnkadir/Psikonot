import re
from datetime import date, datetime, time as clock
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Optional, Dict, Any, List

# What a psychologist writes about a client. Each type (except document/other)
# has a validation schema at the bottom of this file.
RECORD_TYPES = {
    "client_profile":     "Client Profile",
    "session_note":       "Session Note",
    "session_transcript": "Session Transcript",
    "treatment_plan":     "Treatment Plan",
    "homework":       "Homework",
    "consent_form":   "Consent Form",
    "document":       "Document",
    "other":          "Other",
}

def sanitize_html(v: str) -> str:
    """
    Normalises free text on the way in. Deliberately does NOT HTML-escape.

    Clinical text is stored verbatim: a record is a clinical document, and
    rewriting "Dr. Smith & Co" to "Dr. Smith &amp; Co" corrupts it permanently in
    an append-only chain. Escaping belongs at the point of rendering, which the
    web client does for every value it interpolates.
    """
    if isinstance(v, str):
        return v.strip()
    return v

def validate_iso_date(v: str) -> str:
    """Helper to validate ISO 8601 date format and ensure it is not in the future."""
    if not isinstance(v, str):
        raise ValueError("Date must be a string")

    dt = None
    # Try YYYY-MM-DD
    try:
        dt = datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        pass

    if not dt:
        # Try full ISO datetime
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Date must be in ISO 8601 format (e.g., YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)")

    # Check if the date is in the future
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    if dt > now:
        raise ValueError("Date cannot be in the future")
    return v


# ── LOGIN SCHEMAS ───────────────────────────────────────────
class LoginReq(BaseModel):
    username: str
    password: str
    code: Optional[str] = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        if not re.match(r"^[a-zA-Z0-9_\.\-]{3,50}$", v):
            raise ValueError("Username must contain only alphanumeric characters, underscores, dots, or hyphens.")
        return v





class WebAuthnRegisterReq(BaseModel):
    credential_id: str          # base64url rawId from the authenticator
    public_key: str             # base64url SPKI DER (ES256 / secp256r1)
    client_data_json: str       # base64url clientDataJSON of the create() ceremony


class WebAuthnLoginReq(BaseModel):
    credential_id: str
    signature: str
    client_data_json: str
    authenticator_data: str

class RevokePasskeyReq(BaseModel):
    username: str
    credential_id: str


# ── USER CREATION SCHEMAS ───────────────────────────────────

# ── 2FA & SECURITY SCHEMAS ──────────────────────────────────
class Verify2FAReq(BaseModel):
    code: str


# ── HEALTH RECORD SCHEMAS ───────────────────────────────────
class RecordCreate(BaseModel):
    patient_id:      str
    record_type:     str
    title:           str
    doctor_name:     str
    institution:     str
    record_date:     str
    is_confidential: bool = False
    confidential_password: Optional[str] = None
    data:            Dict[str, Any]
    notes:           Optional[str] = None
    file_name:       Optional[str] = None
    file_type:       Optional[str] = None
    file_data:       Optional[str] = None

    @field_validator("doctor_name", "institution", "title", "notes")
    @classmethod
    def sanitize_fields(cls, v):
        return sanitize_html(v)

    @field_validator("record_date")
    @classmethod
    def check_record_date(cls, v):
        return validate_iso_date(v)

    @field_validator("patient_id")
    @classmethod
    def validate_patient_id(cls, v):
        if not re.match(r"^CL-[0-9]{3,}$", v):
            raise ValueError("patient_id must follow format CL-[0-9]{3,} (e.g., CL-001)")
        return v

    @field_validator("record_type")
    @classmethod
    def valid_record_type(cls, v):
        if v not in RECORD_TYPES:
            raise ValueError(f"Invalid record type: {v}")
        return v

    @model_validator(mode="after")
    def confidential_needs_a_password(self):
        # Without one the record is stored as protected with nothing to open it.
        if self.is_confidential and not (self.confidential_password or "").strip():
            raise ValueError("A confidential record needs a password")
        return self

    @field_validator("file_data")
    @classmethod
    def file_data_size(cls, v):
        if v is not None:
            max_len = int(2 * 1024 * 1024 * 4 / 3)
            if len(v) > max_len:
                raise ValueError("Attachment size exceeds the 2MB limit")
            # The web client puts this inside <img src="data:...;base64,HERE">.
            # Real base64 has no quotes or angle brackets to break out with.
            if not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", v):
                raise ValueError("file_data must be base64-encoded")
        return v

    @field_validator("file_type")
    @classmethod
    def file_type_is_a_mime_type(cls, v):
        # Also rendered into that data: URL, so only a plain MIME type is allowed.
        if v is not None:
            v = v.strip().lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*", v):
                raise ValueError("file_type must be a MIME type such as image/png")
        return v

    @field_validator("file_name")
    @classmethod
    def file_name_is_printable(cls, v):
        if v is not None:
            v = v.strip()
            if not v or len(v) > 255 or any(ord(ch) < 32 for ch in v):
                raise ValueError("file_name must be 1-255 printable characters")
        return v


class DecryptRequest(BaseModel):
    password: str


class CorrectionCreate(BaseModel):
    # Full record-shaped payload (title, record_type, data, notes, …) that
    # supersedes the original. The original block is never modified.
    corrected_data: dict
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v):
        if not v or not v.strip():
            raise ValueError("A correction reason is required")
        return v.strip()


# ── DATA SCHEMAS FOR RECORD TYPES ───────────────────────────
def validate_date_format(v: str) -> str:
    """Checks the ISO 8601 format only. Unlike validate_iso_date, a future date
    is allowed — a homework due date is usually in the future."""
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Date must be in ISO 8601 format (e.g., YYYY-MM-DD)")
    return v


class SessionNoteSchema(BaseModel):
    session_number: int
    duration_min: int
    session_format: str
    summary: str

    @field_validator("session_number")
    @classmethod
    def check_session_number(cls, v):
        if v <= 0:
            raise ValueError("Session number must be a positive integer")
        return v

    @field_validator("duration_min")
    @classmethod
    def check_duration(cls, v):
        if not (1 <= v <= 300):
            raise ValueError("Session duration must be between 1 and 300 minutes")
        return v

    @field_validator("session_format")
    @classmethod
    def check_format(cls, v):
        allowed = {"In-person", "Online"}
        if v not in allowed:
            raise ValueError(f"Session format must be one of {allowed}")
        return v

    @field_validator("summary")
    @classmethod
    def sanitize_summary(cls, v):
        return sanitize_html(v)


class ClientProfileSchema(BaseModel):
    """Who the client is and why they came: the page at the front of the file."""
    presenting_problem: str
    characteristics: str = ""
    background: str = ""

    @field_validator("presenting_problem")
    @classmethod
    def require_problem(cls, v):
        v = sanitize_html(v)
        if not v:
            raise ValueError("Presenting problem is required")
        return v

    @field_validator("characteristics", "background")
    @classmethod
    def sanitize_strings(cls, v):
        return sanitize_html(v)


class SessionTranscriptSchema(BaseModel):
    """A written transcript of what was said in a session. Always
    practitioner-only (core/services/access_policy.py)."""
    session_number: int
    transcript: str

    @field_validator("session_number")
    @classmethod
    def check_session_number(cls, v):
        if v <= 0:
            raise ValueError("Session number must be a positive integer")
        return v

    @field_validator("transcript")
    @classmethod
    def require_transcript(cls, v):
        v = sanitize_html(v)
        if not v:
            raise ValueError("Transcript text is required")
        return v


class TreatmentPlanSchema(BaseModel):
    goals: str
    approach: str
    planned_sessions: int

    @field_validator("goals", "approach")
    @classmethod
    def sanitize_strings(cls, v):
        return sanitize_html(v)

    @field_validator("planned_sessions")
    @classmethod
    def check_planned_sessions(cls, v):
        if not (1 <= v <= 200):
            raise ValueError("Planned sessions must be between 1 and 200")
        return v


class HomeworkSchema(BaseModel):
    task: str
    due_date: str

    @field_validator("task")
    @classmethod
    def sanitize_task(cls, v):
        return sanitize_html(v)

    @field_validator("due_date")
    @classmethod
    def check_due_date(cls, v):
        return validate_date_format(v)


class ConsentFormSchema(BaseModel):
    form_type: str
    signed_date: str

    @field_validator("form_type")
    @classmethod
    def sanitize_form_type(cls, v):
        return sanitize_html(v)

    @field_validator("signed_date")
    @classmethod
    def check_signed_date(cls, v):
        return validate_iso_date(v)


# Which schema checks the `data` of each record type. "document" and "other"
# are free-form, so they have no entry here.
DATA_SCHEMAS = {
    "client_profile":     ClientProfileSchema,
    "session_note":       SessionNoteSchema,
    "session_transcript": SessionTranscriptSchema,
    "treatment_plan":     TreatmentPlanSchema,
    "homework":       HomeworkSchema,
    "consent_form":   ConsentFormSchema,
}


# ── Out-of-band onboarding ──────────────────────────────────────────
# Not admin or security_officer: those roles co-sign dual-control requests, and
# the operator who provisions an account holds its enrollment code. One operator
# could make their own second approver. Privileged accounts are not created
# through the API.
_ONBOARDING_ROLES = {"practitioner", "auditor"}


class ProvisionAccountReq(BaseModel):
    """An operator provisions a vetted account; the holder activates it later."""
    username: str
    full_name: str
    role: str
    specialty: Optional[str] = None
    institution: Optional[str] = None
    clearance: Optional[str] = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        if not re.match(r"^[A-Za-z0-9._-]{3,50}$", v or ""):
            raise ValueError("Username must be 3-50 chars: letters, digits, . _ -")
        return v

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        if v not in _ONBOARDING_ROLES:
            raise ValueError(f"Role must be one of {sorted(_ONBOARDING_ROLES)}")
        return v

    @field_validator("full_name", "specialty", "institution", "clearance")
    @classmethod
    def sanitize_strings(cls, v):
        return sanitize_html(v)


class RedeemEnrollmentReq(BaseModel):
    """The account holder redeems a single-use, out-of-band enrollment token."""
    enrollment_token: str
    new_password: str


# ── Account management ──────────────────────────────────────────────
class ChangePasswordReq(BaseModel):
    """A signed-in user replaces their own password; the current one is required."""
    current_password: str
    new_password: str


class RedeemResetReq(BaseModel):
    """The holder of a single-use password reset code chooses a new password."""
    reset_code: str
    new_password: str


class _StartReq(BaseModel):
    """When an appointment starts, in either of two forms:

      * local_date (YYYY-MM-DD) + local_time (HH:MM): the date and time the
        user picked, read in the server's timezone (VHV_TIMEZONE,
        Europe/Istanbul by default) whatever the browser's. The web forms send
        this, so the forms agree with the calendar and the day views;
      * starts_at: an instant with its timezone (e.g.
        2026-10-01T10:00:00+03:00), for API clients.

    When both are sent, local_date + local_time win and starts_at is ignored.
    local_date and local_time come together. Times are stored in UTC
    (backend/routers/appointments.requested_start)."""
    starts_at: Optional[datetime] = None
    local_date: Optional[date] = None
    local_time: Optional[clock] = None

    @field_validator("starts_at")
    @classmethod
    def needs_timezone(cls, v):
        if v is not None and v.tzinfo is None:
            raise ValueError("starts_at must include a timezone, e.g. 2026-10-01T10:00:00+03:00")
        return v

    @field_validator("local_time")
    @classmethod
    def wall_clock_only(cls, v):
        if v is not None and v.tzinfo is not None:
            raise ValueError("local_time is a time of day without a timezone, e.g. 10:00")
        return v

    @model_validator(mode="after")
    def date_and_time_together(self):
        if (self.local_date is None) != (self.local_time is None):
            raise ValueError("Give local_date and local_time together")
        return self

    def _require_start(self):
        if self.starts_at is None and self.local_date is None:
            raise ValueError("Give the start: local_date and local_time, or starts_at")
        return self


class BookAppointmentReq(_StartReq):
    """A new appointment; the start as in _StartReq (required)."""
    patient_id: str
    duration_min: int = 50
    session_format: str = "In-person"
    room_id: Optional[str] = None   # in-person only; optional

    @field_validator("room_id")
    @classmethod
    def room_id_shape(cls, v):
        return _room_id(v)

    @model_validator(mode="after")
    def needs_a_start(self):
        return self._require_start()


class ApplicationBookReq(_StartReq):
    """The first appointment of a converted application
    (POST /api/v1/applications/{id}/book): the client and the practitioner
    come from the application."""
    model_config = ConfigDict(extra="forbid")

    duration_min: int = 50
    session_format: str = "In-person"
    room_id: Optional[str] = None   # in-person only; optional

    @field_validator("room_id")
    @classmethod
    def room_id_shape(cls, v):
        return _room_id(v)

    @model_validator(mode="after")
    def needs_a_start(self):
        return self._require_start()


class MoveFollowingReq(_StartReq):
    """Move a session of a series and every later scheduled one: the new
    start of this session (as in _StartReq; left out, the times stay) and/or
    a room for all of them (room_id null takes them out of their room; left
    out, each keeps its own). Every later session keeps its distance in days
    from this one, so the weekly / every-two-weeks spacing stays."""
    room_id: Optional[str] = None

    @field_validator("room_id")
    @classmethod
    def room_id_shape(cls, v):
        return _room_id(v)


class SeriesReq(BookAppointmentReq):
    """A weekly series (core/services/appointment_book.py): the first session
    as for a single booking, how often (weekly / biweekly), and either the
    number of sessions or the last date (YYYY-MM-DD in the practice's
    timezone, inclusive). `skip` lists the dates, in that timezone, to leave
    out, normally the ones the preview showed as taken."""
    frequency: str = "weekly"
    count: Optional[int] = None
    until: Optional[date] = None
    skip: List[date] = Field(default_factory=list, max_length=60)


class UpdateAppointmentReq(_StartReq):
    """Move an appointment (the start as in _StartReq / duration_min /
    room_id) or change its status (cancelled, completed, no_show). A room_id
    of null takes the appointment out of its room; leaving room_id out keeps
    it."""
    duration_min: Optional[int] = None
    room_id: Optional[str] = None
    status: Optional[str] = None

    @field_validator("room_id")
    @classmethod
    def room_id_shape(cls, v):
        return _room_id(v)


def _room_id(v):
    if v is not None and not re.fullmatch(r"[A-Za-z0-9-]{1,40}", v):
        raise ValueError("Not a room id")
    return v


def _room_name(v):
    from core.services.rooms import clean_name
    return clean_name(v)


class RoomReq(BaseModel):
    """A therapy room: a short label such as "Oda 1"."""
    name: str

    @field_validator("name")
    @classmethod
    def short_label(cls, v):
        return _room_name(v)


class UpdateRoomReq(BaseModel):
    """Rename a room and/or switch it off (or back on). Rooms are never deleted."""
    name: Optional[str] = None
    active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def short_label(cls, v):
        return None if v is None else _room_name(v)


class InviteSecretaryReq(BaseModel):
    """A practitioner invites the secretary who will run their appointment book."""
    username: str
    full_name: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        if not re.match(r"^[A-Za-z0-9._-]{3,50}$", v or ""):
            raise ValueError("Username must be 3-50 chars: letters, digits, . _ -")
        return v

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v):
        v = (v or "").strip()
        if not 2 <= len(v) <= 100:
            raise ValueError("Full name must be 2-100 characters")
        return sanitize_html(v)


class StaffLinkReq(BaseModel):
    """Add an existing secretary to a practitioner's book (operators only), or
    remove them from it. A practitioner removes from their own book only
    (practitioner left out or their own username); an operator names the
    practitioner."""
    username: str
    practitioner: Optional[str] = None

    @field_validator("username", "practitioner")
    @classmethod
    def validate_username(cls, v):
        if v is not None and not re.match(r"^[A-Za-z0-9._-]{3,50}$", v):
            raise ValueError("Username must be 3-50 chars: letters, digits, . _ -")
        return v



def _clean_full_name(v):
    v = (v or "").strip()
    if not 2 <= len(v) <= 100:
        raise ValueError("Full name must be 2-100 characters")
    return sanitize_html(v)


def _clean_phone(v):
    v = (v or "").strip()
    if v and not re.fullmatch(r"\+?[0-9 ()-]{7,20}", v):
        raise ValueError("Phone may contain digits, spaces, ( ) - and a leading +")
    return v or None


def _clean_email(v):
    v = (v or "").strip()
    if v and not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,100}\.[A-Za-z]{2,}", v):
        raise ValueError("Not a valid e-mail address")
    return v or None


class ClientCardReq(BaseModel):
    """A client card: who the client is and how to reach them. Nothing clinical
    — what the client talks about goes into their encrypted file."""
    full_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    kvkk_signed_on: Optional[str] = None   # the day the KVKK forms were signed on paper

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v):
        return _clean_full_name(v)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        return _clean_phone(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        return _clean_email(v)

    @field_validator("kvkk_signed_on")
    @classmethod
    def validate_kvkk_date(cls, v):
        v = (v or "").strip()
        if not v:
            return None
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v):
            raise ValueError("Use YYYY-MM-DD")
        return validate_iso_date(v)


class ClientCardUpdateReq(ClientCardReq):
    """The same fields, all optional: only those sent are changed."""
    full_name: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v):
        return None if v is None else _clean_full_name(v)


class ErasureRequestReq(BaseModel):
    patient_id: str

    @field_validator("patient_id")
    @classmethod
    def validate_patient_id(cls, v):
        if not re.match(r"^CL-[0-9]{3,}$", v):
            raise ValueError("patient_id must follow format CL-[0-9]{3,} (e.g., CL-001)")
        return v


# ── INTAKE (applications) ─────────────────────────────────────
# Fixed codes only, and no free-text field: the applications table is not
# encrypted, so nothing clinical may be typed into it. extra="forbid" refuses
# any field these schemas do not name (a "note", a "complaint"...). The code
# lists live in core/services/intake.py.

# A name: letters, with a space, dot, apostrophe or hyphen between them. No
# digits, brackets or colons — "Ayşe (panik)" is not a name.
_APPLICANT_NAME = re.compile(r"[^\W\d_]+(?:[ .'’-]{1,2}[^\W\d_]+)*\.?")
_USERNAME = re.compile(r"[A-Za-z0-9._-]{3,50}")


def _applicant_name(v):
    v = " ".join((v or "").split())
    if not 2 <= len(v) <= 100:
        raise ValueError("Full name must be 2-100 characters")
    if not _APPLICANT_NAME.fullmatch(v):
        raise ValueError("A name may contain only letters, spaces and . ' -")
    return v


def _code(v, allowed, what):
    if v is not None and v not in allowed:
        raise ValueError(f"Unknown {what}")
    return v


def _code_list(v, allowed, what):
    if v is None:
        return v
    for item in v:
        _code(item, allowed, what)
    return [c for c in allowed if c in v]


class ApplicationReq(BaseModel):
    """A request for an appointment from someone who is not a client yet."""
    model_config = ConfigDict(extra="forbid")

    channel: str
    full_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    preferred_practitioner: Optional[str] = None
    assigned_practitioner: Optional[str] = None
    session_format: str = "either"
    preferred_days: List[str] = []
    preferred_times: List[str] = []
    priority: str = "normal"

    @field_validator("channel")
    @classmethod
    def v_channel(cls, v):
        from core.services.intake import CHANNELS
        return _code(v, CHANNELS, "channel")

    @field_validator("full_name")
    @classmethod
    def v_name(cls, v):
        return _applicant_name(v)

    @field_validator("phone")
    @classmethod
    def v_phone(cls, v):
        return _clean_phone(v)

    @field_validator("email")
    @classmethod
    def v_email(cls, v):
        return _clean_email(v)

    @field_validator("preferred_practitioner", "assigned_practitioner")
    @classmethod
    def v_practitioner(cls, v):
        if v is not None and not _USERNAME.fullmatch(v):
            raise ValueError("Not a username")
        return v

    @field_validator("session_format")
    @classmethod
    def v_format(cls, v):
        from core.services.intake import FORMATS
        return _code(v, FORMATS, "session format")

    @field_validator("preferred_days")
    @classmethod
    def v_days(cls, v):
        from core.services.intake import DAYS
        return _code_list(v, DAYS, "day")

    @field_validator("preferred_times")
    @classmethod
    def v_times(cls, v):
        from core.services.intake import TIMES
        return _code_list(v, TIMES, "time of day")

    @field_validator("priority")
    @classmethod
    def v_priority(cls, v):
        from core.services.intake import PRIORITIES
        return _code(v, PRIORITIES, "priority")


class ApplicationUpdateReq(ApplicationReq):
    """The same fields, all optional: only those sent are changed."""
    channel: Optional[str] = None
    full_name: Optional[str] = None
    session_format: Optional[str] = None
    preferred_days: Optional[List[str]] = None
    preferred_times: Optional[List[str]] = None
    priority: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def v_name(cls, v):
        return None if v is None else _applicant_name(v)


class ApplicationStatusReq(BaseModel):
    """contacted, waitlisted, declined (with a reason code) or withdrawn."""
    model_config = ConfigDict(extra="forbid")

    status: str
    reason: Optional[str] = None

    @field_validator("status")
    @classmethod
    def v_status(cls, v):
        from core.services.intake import STATUSES
        return _code(v, STATUSES, "status")

    @field_validator("reason")
    @classmethod
    def v_reason(cls, v):
        from core.services.intake import REASONS
        return _code(v, REASONS, "reason")
