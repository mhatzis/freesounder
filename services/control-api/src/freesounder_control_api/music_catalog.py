"""Curated music-service catalog and helpers to sanitize Music Assistant config."""

from __future__ import annotations

from typing import Any

from freesounder_control_api.models import FormField, FormOption, MusicServiceType

# Official Music Assistant domain ids for the services FreeSounder surfaces in-app.
# Availability on a given household is filled from the connected MA manifests.
CURATED_SERVICES: tuple[MusicServiceType, ...] = (
    MusicServiceType(
        domain="spotify",
        name="Spotify",
        category="streaming",
        auth_kind="oauth",
        description="Browse and play Spotify in the FreeSounder app. Spotify Premium is "
        "needed for Connect and for in-app playback through Music Assistant.",
        documentation="https://www.music-assistant.io/music-providers/spotify/",
        multi_instance=False,
    ),
    MusicServiceType(
        domain="tidal",
        name="Tidal",
        category="streaming",
        auth_kind="oauth",
        description="Browse and play Tidal. Sign-in may ask you to confirm a redirect URL "
        "after logging in at tidal.com.",
        documentation="https://www.music-assistant.io/music-providers/tidal/",
    ),
    MusicServiceType(
        domain="apple_music",
        name="Apple Music",
        category="streaming",
        auth_kind="oauth",
        description="Browse and play Apple Music.",
        documentation="https://www.music-assistant.io/music-providers/apple-music/",
        limited=True,
        limitation=(
            "Apple Music in Music Assistant is limited and experimental. Playback is not "
            "officially supported by Apple. Maximum quality is AAC 256 kbps; lossless and "
            "Atmos are not available. Sign-in tokens expire after about 180 days, and there "
            "can be gaps of up to five seconds between tracks."
        ),
    ),
    MusicServiceType(
        domain="deezer",
        name="Deezer",
        category="streaming",
        auth_kind="oauth",
        description="Browse and play Deezer.",
        documentation="https://www.music-assistant.io/music-providers/deezer/",
    ),
    MusicServiceType(
        domain="ytmusic",
        name="YouTube Music",
        category="streaming",
        auth_kind="credentials",
        description="Browse and play YouTube Music.",
        documentation="https://www.music-assistant.io/music-providers/youtube-music/",
    ),
    MusicServiceType(
        domain="qobuz",
        name="Qobuz",
        category="streaming",
        auth_kind="credentials",
        description="Browse and play Qobuz with a username and password.",
        documentation="https://www.music-assistant.io/music-providers/qobuz/",
    ),
    MusicServiceType(
        domain="soundcloud",
        name="SoundCloud",
        category="streaming",
        auth_kind="credentials",
        description="Browse and play SoundCloud.",
        documentation="https://www.music-assistant.io/music-providers/soundcloud/",
    ),
    MusicServiceType(
        domain="tunein",
        name="TuneIn",
        category="radio",
        auth_kind="none",
        description="Internet radio through TuneIn.",
        documentation="https://www.music-assistant.io/music-providers/tunein/",
    ),
    MusicServiceType(
        domain="radiobrowser",
        name="Radio Browser",
        category="radio",
        auth_kind="none",
        description="A free directory of radio stations, no account needed.",
        documentation="https://www.music-assistant.io/music-providers/radiobrowser/",
    ),
    MusicServiceType(
        domain="plex",
        name="Plex",
        category="media_server",
        auth_kind="credentials",
        description="Play music from a Plex Media Server.",
        documentation="https://www.music-assistant.io/music-providers/plex/",
    ),
    MusicServiceType(
        domain="jellyfin",
        name="Jellyfin",
        category="media_server",
        auth_kind="credentials",
        description="Play music from a Jellyfin server.",
        documentation="https://www.music-assistant.io/music-providers/jellyfin/",
    ),
    MusicServiceType(
        domain="opensubsonic",
        name="OpenSubsonic",
        category="media_server",
        auth_kind="credentials",
        description="Play music from a Subsonic-compatible server, such as Navidrome or Airsonic.",
        documentation="https://www.music-assistant.io/music-providers/opensubsonic/",
    ),
    MusicServiceType(
        domain="filesystem_local",
        name="Local files",
        category="files",
        auth_kind="path",
        description="A music folder on the household host.",
        documentation="https://www.music-assistant.io/music-providers/filesystem/",
        multi_instance=True,
    ),
    MusicServiceType(
        domain="filesystem_smb",
        name="SMB share",
        category="files",
        auth_kind="credentials",
        description="A music library on a network share (SMB or CIFS).",
        documentation="https://www.music-assistant.io/music-providers/filesystem/",
        multi_instance=True,
    ),
    MusicServiceType(
        domain="filesystem_nfs",
        name="NFS share",
        category="files",
        auth_kind="path",
        description="A music library on an NFS share, when Music Assistant supports NFS.",
        documentation="https://www.music-assistant.io/music-providers/filesystem/",
        multi_instance=True,
    ),
)

CURATED_BY_DOMAIN: dict[str, MusicServiceType] = {item.domain: item for item in CURATED_SERVICES}

# Fields that may identify an account without being a secret.
_ACCOUNT_KEYS = (
    "username",
    "user",
    "email",
    "user_id",
    "userid",
    "account",
    "login",
    "display_name",
)

_SECURE_TYPES = {"secure_string"}
_DROPDOWN_WHEN_OPTIONS = {"string", "integer", "float", "splitted_string"}

CALLBACK_SCHEME = "freesounder"
CALLBACK_PATH = "music-services/auth"


def catalog_item_for(
    domain: str,
    *,
    available_on_server: bool = False,
    ma_stage: str | None = None,
    configured: bool = False,
    builtin: bool = False,
    multi_instance: bool | None = None,
    documentation: str | None = None,
) -> MusicServiceType:
    """Return a curated type, or a generic fallback for an unknown MA domain."""

    base = CURATED_BY_DOMAIN.get(domain)
    if base is None:
        base = MusicServiceType(
            domain=domain,
            name=domain.replace("_", " ").title(),
            category="other",
            auth_kind="credentials",
            available_on_server=available_on_server,
            ma_stage=ma_stage,
            configured=configured,
            builtin=builtin,
            multi_instance=bool(multi_instance),
            documentation=documentation,
        )
        return base
    return base.model_copy(
        update={
            "available_on_server": available_on_server,
            "ma_stage": ma_stage or base.ma_stage,
            "configured": configured,
            "builtin": builtin,
            "multi_instance": base.multi_instance
            if multi_instance is None
            else multi_instance,
            "documentation": documentation or base.documentation,
        }
    )


def _entry_type_name(raw: Any) -> str:
    if raw is None:
        return "string"
    value = getattr(raw, "value", raw)
    return str(value)


def _is_secret_type(type_name: str) -> bool:
    return type_name in _SECURE_TYPES or type_name == "secure_string"


def config_entry_to_field(entry: Any) -> FormField:
    """Map a Music Assistant ConfigEntry (object or dict) to a FormField, redacting secrets."""

    data = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
    type_name = _entry_type_name(data.get("type"))
    options_raw = data.get("options") or []
    options: list[FormOption] = []
    for option in options_raw:
        if hasattr(option, "to_dict"):
            option = option.to_dict()
        if isinstance(option, dict):
            title = option.get("title") or str(option.get("value", ""))
            options.append(
                FormOption(
                    value=option.get("value"),
                    title=str(title),
                    disabled=bool(option.get("disabled", False)),
                    description=option.get("description"),
                )
            )
    display_type = type_name
    if options and type_name in _DROPDOWN_WHEN_OPTIONS:
        display_type = "dropdown"
    raw_value = data.get("value")
    if raw_value is None:
        raw_value = getattr(entry, "value", None)
    has_value = raw_value not in (None, "")
    secret = _is_secret_type(type_name)
    value = None if secret else raw_value
    default = data.get("default_value")
    if secret:
        default = None
    return FormField(
        key=str(data.get("key") or ""),
        type=display_type,
        label=data.get("label") or data.get("action_label"),
        description=data.get("description"),
        required=bool(data.get("required", False)),
        value=value,
        has_value=has_value,
        secret=secret,
        options=options,
        hidden=bool(data.get("hidden", False)),
        advanced=bool(data.get("advanced", False)),
        read_only=bool(data.get("read_only", False)),
        default=default,
    )


def fields_from_entries(entries: Any) -> list[FormField]:
    if not entries:
        return []
    return [config_entry_to_field(entry) for entry in entries]


def visible_values(fields: list[FormField]) -> dict[str, Any]:
    """Non-secret values the app may display."""

    out: dict[str, Any] = {}
    for field in fields:
        if field.secret or field.hidden or field.type in {"label", "divider", "action"}:
            continue
        if field.value is not None:
            out[field.key] = field.value
    return out


def account_from_fields(fields: list[FormField]) -> str | None:
    values = {field.key: field.value for field in fields if not field.secret}
    for key in _ACCOUNT_KEYS:
        value = values.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def map_provider_status(raw: Any, *, available: bool = False, enabled: bool = True) -> str:
    """Normalize MA ProviderStatus (or a missing status) to loaded/error/needs_reauth/..."""

    if raw is None:
        if not enabled:
            return "disabled"
        return "loaded" if available else "error"
    value = getattr(raw, "value", raw)
    name = str(value).lower()
    if name in {"auth_required", "needs_reauth", "authentication_required"}:
        return "needs_reauth"
    if name in {"loaded", "loading", "disabled", "incompatible", "error"}:
        return name
    return name or "error"


def last_error_message(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    message = getattr(raw, "message", None)
    if message:
        return str(message)
    if hasattr(raw, "to_dict"):
        raw = raw.to_dict()
    if isinstance(raw, dict):
        return raw.get("message") or raw.get("error")
    return str(raw)
