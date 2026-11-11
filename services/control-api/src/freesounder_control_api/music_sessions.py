"""In-memory Music Assistant setup/auth sessions for the app-driven OAuth flow."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from freesounder_control_api.models import AuthSession
from freesounder_control_api.music_catalog import (
    CALLBACK_PATH,
    CALLBACK_SCHEME,
    fields_from_entries,
)

EventHook = Callable[[str, Any], Awaitable[None]]


def new_session_id() -> str:
    return uuid.uuid4().hex


def step_type_name(step: Any) -> str:
    typ = getattr(step, "type", None)
    if typ is None and isinstance(step, dict):
        typ = step.get("type")
    return str(getattr(typ, "value", typ) or "")


def session_from_step(
    *,
    session_id: str,
    domain: str,
    instance_id: str | None,
    step: Any,
    error: str | None = None,
) -> AuthSession:
    """Map a Music Assistant SetupFlowStep (object or dict) to an AuthSession."""

    data = step.to_dict() if hasattr(step, "to_dict") else dict(step or {})
    typ = step_type_name(step)
    flow_id = data.get("flow_id") or getattr(step, "flow_id", None)
    status = "pending"
    if typ == "form":
        status = "awaiting_form"
    elif typ == "external":
        status = "awaiting_browser"
    elif typ == "progress":
        status = "progress"
    elif typ == "finish":
        status = "success"
        result = data.get("result") or {}
        if isinstance(result, dict):
            instance_id = result.get("instance_id") or instance_id
    elif typ == "abort":
        status = "error"
        error = error or data.get("reason") or "Sign-in was cancelled"
    if error and typ not in {"finish"}:
        status = "error"
    entries = fields_from_entries(data.get("entries") or getattr(step, "entries", None) or [])
    return AuthSession(
        session_id=session_id,
        status=status,  # type: ignore[arg-type]
        domain=domain,
        instance_id=instance_id,
        flow_id=str(flow_id) if flow_id else None,
        auth_url=data.get("url") or getattr(step, "url", None),
        callback_scheme=CALLBACK_SCHEME,
        callback_url=f"{CALLBACK_SCHEME}://{CALLBACK_PATH}",
        title=data.get("title"),
        description=data.get("description"),
        entries=entries,
        errors=dict(data.get("errors") or {}),
        last_step=data.get("last_step"),
        copy_text=data.get("copy_text"),
        progress_text=data.get("progress_text"),
        image=data.get("image"),
        error=error,
    )


class AuthStore:
    """Short-lived auth/setup sessions keyed by session_id and MA flow_id."""

    def __init__(self) -> None:
        self._by_id: dict[str, AuthSession] = {}

    def put(self, session: AuthSession) -> AuthSession:
        self._by_id[session.session_id] = session
        return session

    def get(self, session_id: str) -> AuthSession | None:
        return self._by_id.get(session_id)

    def find_by_flow(self, flow_id: str) -> AuthSession | None:
        for session in self._by_id.values():
            if session.flow_id == flow_id:
                return session
        return None

    def discard(self, session_id: str) -> None:
        self._by_id.pop(session_id, None)

    def pending(self) -> list[AuthSession]:
        return [
            session
            for session in self._by_id.values()
            if session.status in {"pending", "awaiting_form", "awaiting_browser", "progress"}
        ]



class MusicServiceError(Exception):
    """Domain error mapped to an HTTP status by the facade."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
