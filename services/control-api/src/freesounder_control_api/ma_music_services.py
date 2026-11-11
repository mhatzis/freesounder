"""Music Assistant calls that back the in-app Music services screen."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from music_assistant_models.enums import EventType

from freesounder_control_api.backend import MusicAssistantUnavailable
from freesounder_control_api.models import (
    AddMusicServiceBody,
    AuthSession,
    MusicService,
    MusicServiceType,
    SpotifyConnectRoom,
    SpotifyConnectStatus,
    StartAuthBody,
    SubmitAuthBody,
    UpdateMusicServiceBody,
)
from freesounder_control_api.music_catalog import (
    CURATED_BY_DOMAIN,
    account_from_fields,
    catalog_item_for,
    fields_from_entries,
    last_error_message,
    map_provider_status,
    visible_values,
)
from freesounder_control_api.music_sessions import (
    AuthStore,
    MusicServiceError,
    new_session_id,
    session_from_step,
)

logger = logging.getLogger(__name__)

EventHook = Callable[[str, Any], Awaitable[None]]

SPOTIFY_CONNECT_DOMAIN = "spotify_connect"
CONF_CONNECTED_PLAYERS = "connected_players"
CONF_BACKEND = "backend"

_ENGINE_LABELS = {
    "go_librespot": "go-librespot",
    "soloist": "Spotify Soloist",
}


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return dict(value) if isinstance(value, dict) else {}


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        return default
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


class MaMusicServices:
    """Provider setup, options and Spotify Connect, talking only through the MA client."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self._auth = AuthStore()
        self._listener: EventHook | None = None
        self._subscribed = False

    @property
    def client(self) -> Any:
        return self._backend.client

    def bind_listener(self, listener: EventHook) -> None:
        self._listener = listener

    async def _emit(self, event: str, data: Any) -> None:
        if self._listener is not None:
            await self._listener(event, data)

    def attach_events(self) -> None:
        if self._subscribed or self._backend._client is None:
            return
        self.client.subscribe(
            self._on_event,
            event_filter=(
                EventType.SETUP_FLOW_UPDATED,
                EventType.PROVIDERS_UPDATED,
                EventType.AUTH_SESSION,
            ),
        )
        self._subscribed = True

    def detach_events(self) -> None:
        """Forget the subscription so a reconnect subscribes again."""

        self._subscribed = False

    def _on_event(self, event: Any) -> None:
        typ = getattr(event, "event", None)
        typ_name = str(getattr(typ, "value", typ) or "")
        try:
            loop = getattr(self._backend, "_loop", None)
            import asyncio

            asyncio.get_running_loop().create_task(self._handle_event(typ_name, event))
            del loop
        except Exception:  # noqa: BLE001
            logger.debug("music service event dispatch failed", exc_info=True)

    async def _handle_event(self, typ_name: str, event: Any) -> None:
        if typ_name == "setup_flow_updated":
            data = getattr(event, "data", None)
            flow_id = getattr(event, "object_id", None)
            if isinstance(data, dict) and not flow_id:
                flow_id = data.get("flow_id")
            if hasattr(data, "flow_id") and not flow_id:
                flow_id = data.flow_id
            session = self._auth.find_by_flow(str(flow_id)) if flow_id else None
            if session is None:
                return
            updated = session_from_step(
                session_id=session.session_id,
                domain=session.domain,
                instance_id=session.instance_id,
                step=data,
            )
            self._auth.put(updated)
            await self._emit("music_service_auth", updated.model_dump())
            if updated.status == "success":
                await self._emit(
                    "music_services",
                    [item.model_dump() for item in await self.list_music_services()],
                )
        elif typ_name == "providers_updated":
            await self._emit(
                "music_services",
                [item.model_dump() for item in await self.list_music_services()],
            )

    def _manifests(self) -> list[Any]:
        try:
            return list(self.client.provider_manifests)
        except Exception:  # noqa: BLE001
            return []

    def _instances(self) -> list[Any]:
        try:
            return list(self.client.providers)
        except Exception:  # noqa: BLE001
            return []

    def _manifest_map(self) -> dict[str, Any]:
        return {_attr(item, "domain"): item for item in self._manifests()}

    async def list_music_service_types(self) -> list[MusicServiceType]:
        manifests = self._manifest_map()
        configured_domains = {
            _attr(item, "domain") for item in self._instances() if _attr(item, "type", default="")
        }
        # Prefer provider configs so disabled instances still count as configured.
        try:
            configs = await self.client.config.get_provider_configs()
            configured_domains = {_attr(item, "domain") for item in configs}
        except Exception:  # noqa: BLE001
            logger.debug("get_provider_configs failed", exc_info=True)
        items: list[MusicServiceType] = []
        seen: set[str] = set()
        for curated in CURATED_BY_DOMAIN.values():
            manifest = manifests.get(curated.domain)
            stage = str(_attr(manifest, "stage", default="") or "") or None
            if stage:
                stage = getattr(stage, "value", stage)
                stage = str(stage)
            items.append(
                catalog_item_for(
                    curated.domain,
                    available_on_server=manifest is not None,
                    ma_stage=stage or ("experimental" if curated.limited else None),
                    configured=curated.domain in configured_domains and not curated.multi_instance,
                    builtin=bool(_attr(manifest, "builtin", default=False)),
                    multi_instance=_attr(manifest, "multi_instance", default=None),
                    documentation=_attr(manifest, "documentation", default=None),
                )
            )
            seen.add(curated.domain)
        return items

    async def _config_for(self, instance_id: str) -> Any:
        try:
            return await self.client.config.get_provider_config(instance_id)
        except MusicAssistantUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MusicServiceError(404, f"Music service not found: {instance_id}") from exc

    async def _entries_for(self, instance_id: str) -> list[Any]:
        try:
            return await self.client.config.get_provider_config_entries(instance_id)
        except Exception:  # noqa: BLE001
            logger.debug("get_provider_config_entries failed for %s", instance_id, exc_info=True)
            config = await self._config_for(instance_id)
            values = _attr(config, "values", default={}) or {}
            if isinstance(values, dict):
                first = next(iter(values.values()), None)
                if values and not isinstance(first, (str, int, float, bool, type(None))):
                    return list(values.values())
                return []
            return []

    def _service_from_config(self, config: Any, entries: list[Any] | None = None) -> MusicService:
        domain = str(_attr(config, "domain"))
        instance_id = str(_attr(config, "instance_id"))
        curated = catalog_item_for(domain)
        fields = fields_from_entries(entries) if entries is not None else fields_from_entries(
            list((_attr(config, "values") or {}).values())
            if isinstance(_attr(config, "values"), dict)
            else []
        )
        # If values is a dict of raw values not ConfigEntry, skip.
        if not fields and isinstance(_attr(config, "values"), dict):
            raw_vals = _attr(config, "values")
            if raw_vals and not hasattr(next(iter(raw_vals.values()), None), "to_dict"):
                fields = []
        instance = next(
            (item for item in self._instances() if str(_attr(item, "instance_id")) == instance_id),
            None,
        )
        available = bool(_attr(instance, "available", default=False))
        enabled = bool(_attr(config, "enabled", default=True))
        status = map_provider_status(
            _attr(config, "status"), available=available, enabled=enabled
        )
        error = last_error_message(_attr(config, "last_error"))
        name = _attr(config, "name") or _attr(config, "default_name") or curated.name
        return MusicService(
            instance_id=instance_id,
            domain=domain,
            name=str(name),
            type=str(getattr(_attr(config, "type"), "value", _attr(config, "type") or "music")),
            status=status,
            available=available,
            enabled=enabled,
            account=account_from_fields(fields),
            error=error,
            limited=curated.limited,
            limitation=curated.limitation,
            auth_kind=curated.auth_kind,
            can_reauthenticate=curated.auth_kind == "oauth" or status == "needs_reauth",
            values=visible_values(fields),
            entries=fields,
        )

    async def list_music_services(self) -> list[MusicService]:
        try:
            configs = await self.client.config.get_provider_configs(include_values=True)
        except TypeError:
            configs = await self.client.config.get_provider_configs()
        except MusicAssistantUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MusicServiceError(502, f"Music Assistant is not reachable: {exc}") from exc
        services: list[MusicService] = []
        for config in configs:
            typ = _attr(config, "type")
            typ_name = str(getattr(typ, "value", typ) or "")
            domain = str(_attr(config, "domain") or "")
            # Surface curated music sources plus Spotify Connect as a configured plugin.
            if typ_name not in {"", "music", "plugin"}:
                continue
            if typ_name == "plugin" and domain != SPOTIFY_CONNECT_DOMAIN:
                continue
            is_curated = typ_name == "music" or domain in CURATED_BY_DOMAIN
            if is_curated or domain == SPOTIFY_CONNECT_DOMAIN:
                entries = None
                try:
                    entries = await self._entries_for(str(_attr(config, "instance_id")))
                except Exception:  # noqa: BLE001
                    entries = None
                services.append(self._service_from_config(config, entries))
        return services

    async def get_music_service(self, instance_id: str) -> MusicService:
        config = await self._config_for(instance_id)
        entries = await self._entries_for(instance_id)
        return self._service_from_config(config, entries)

    async def add_music_service(self, body: AddMusicServiceBody) -> AuthSession | MusicService:
        if not body.domain:
            raise MusicServiceError(422, "Provide a service domain")
        # OAuth (and empty credential forms) start a setup flow the app can drive.
        if not body.values:
            return await self.start_music_service_auth(StartAuthBody(domain=body.domain))
        # Try the guided setup first so values land in setup_data when MA requires it.
        try:
            session = await self.start_music_service_auth(StartAuthBody(domain=body.domain))
        except MusicServiceError:
            session = None
        if session is not None and session.status == "awaiting_form":
            return await self.submit_music_service_auth(
                session.session_id, SubmitAuthBody(values=body.values)
            )
        if session is not None and session.status == "success" and session.instance_id:
            if body.values:
                try:
                    return await self.update_music_service(
                        session.instance_id,
                        UpdateMusicServiceBody(values=body.values, name=body.name),
                    )
                except MusicServiceError:
                    return await self.get_music_service(session.instance_id)
            return await self.get_music_service(session.instance_id)
        if session is not None and session.status in {
            "awaiting_browser",
            "progress",
            "awaiting_form",
        }:
            return session
        # Fallback for older Music Assistant: config/providers/save.
        try:
            config = await self.client.config.save_provider_config(
                provider_domain=body.domain,
                values=body.values,
                instance_id=None,
            )
        except MusicAssistantUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MusicServiceError(400, str(exc)) from exc
        instance_id = str(_attr(config, "instance_id"))
        await self._emit(
            "music_services", [item.model_dump() for item in await self.list_music_services()]
        )
        return await self.get_music_service(instance_id)

    async def update_music_service(
        self, instance_id: str, body: UpdateMusicServiceBody
    ) -> MusicService:
        config = await self._config_for(instance_id)
        domain = str(_attr(config, "domain"))
        values = dict(body.values)
        if body.name is not None:
            values.setdefault("name", body.name)
        if body.enabled is not None:
            values.setdefault("enabled", body.enabled)
        try:
            await self.client.config.save_provider_config(
                provider_domain=domain,
                values=values,
                instance_id=instance_id,
            )
        except MusicAssistantUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MusicServiceError(400, str(exc)) from exc
        await self._emit(
            "music_services", [item.model_dump() for item in await self.list_music_services()]
        )
        return await self.get_music_service(instance_id)

    async def remove_music_service(self, instance_id: str) -> None:
        await self._config_for(instance_id)
        try:
            await self.client.config.remove_provider_config(instance_id)
        except MusicAssistantUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MusicServiceError(400, str(exc)) from exc
        await self._emit(
            "music_services", [item.model_dump() for item in await self.list_music_services()]
        )

    async def start_music_service_auth(self, body: StartAuthBody) -> AuthSession:
        if not body.domain and not body.instance_id:
            raise MusicServiceError(422, "Provide a service domain or instance id")
        session_id = new_session_id()
        domain = body.domain or ""
        instance_id = body.instance_id
        try:
            if instance_id:
                config = await self._config_for(instance_id)
                domain = str(_attr(config, "domain"))
                step = await self.client.config.reconfigure_provider(instance_id)
            else:
                step = await self.client.config.setup_provider(domain)
        except MusicAssistantUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MusicServiceError(400, f"Could not start sign-in: {exc}") from exc
        session = session_from_step(
            session_id=session_id,
            domain=domain,
            instance_id=instance_id,
            step=step,
        )
        self._auth.put(session)
        if session.status == "success":
            await self._emit("music_service_auth", session.model_dump())
            await self._emit(
                "music_services", [item.model_dump() for item in await self.list_music_services()]
            )
        return session

    async def get_music_service_auth(self, session_id: str) -> AuthSession:
        session = self._auth.get(session_id)
        if session is None:
            raise MusicServiceError(404, f"Auth session not found: {session_id}")
        if session.flow_id and session.status in {
            "pending",
            "awaiting_form",
            "awaiting_browser",
            "progress",
        }:
            try:
                step = await self.client.config.get_setup_flow(session.flow_id)
                session = session_from_step(
                    session_id=session.session_id,
                    domain=session.domain,
                    instance_id=session.instance_id,
                    step=step,
                )
                self._auth.put(session)
            except Exception:  # noqa: BLE001
                logger.debug("get_setup_flow failed for %s", session.flow_id, exc_info=True)
        return session

    async def submit_music_service_auth(
        self, session_id: str, body: SubmitAuthBody
    ) -> AuthSession:
        session = await self.get_music_service_auth(session_id)
        if not session.flow_id:
            raise MusicServiceError(400, "This sign-in session has no form to submit")
        try:
            step = await self.client.config.submit_setup_flow(session.flow_id, body.values)
        except MusicAssistantUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MusicServiceError(400, str(exc)) from exc
        session = session_from_step(
            session_id=session.session_id,
            domain=session.domain,
            instance_id=session.instance_id,
            step=step,
        )
        self._auth.put(session)
        await self._emit("music_service_auth", session.model_dump())
        if session.status == "success":
            await self._emit(
                "music_services", [item.model_dump() for item in await self.list_music_services()]
            )
        return session

    async def abort_music_service_auth(self, session_id: str) -> AuthSession:
        session = await self.get_music_service_auth(session_id)
        if session.flow_id:
            try:
                await self.client.config.abort_setup_flow(session.flow_id)
            except Exception:  # noqa: BLE001
                logger.debug("abort_setup_flow failed", exc_info=True)
        session = session.model_copy(update={"status": "aborted", "error": "Cancelled"})
        self._auth.put(session)
        await self._emit("music_service_auth", session.model_dump())
        return session

    async def complete_music_service_auth_callback(
        self, session_id: str, params: dict[str, str]
    ) -> AuthSession:
        """Called when the in-app browser returns; MA usually already finished via its callback."""

        del params
        return await self.get_music_service_auth(session_id)

    async def poll_music_service_auth(self) -> list[AuthSession]:
        updated: list[AuthSession] = []
        for session in list(self._auth.pending()):
            if not session.flow_id:
                continue
            try:
                refreshed = await self.get_music_service_auth(session.session_id)
            except MusicServiceError:
                continue
            if refreshed.status != session.status or refreshed.auth_url != session.auth_url:
                updated.append(refreshed)
                await self._emit("music_service_auth", refreshed.model_dump())
                if refreshed.status == "success":
                    await self._emit(
                        "music_services",
                        [item.model_dump() for item in await self.list_music_services()],
                    )
        return updated

    def _player_name(self, player_id: str) -> str:
        try:
            player = self.client.players.get(player_id)
        except Exception:  # noqa: BLE001
            player = None
        if player is not None:
            return str(_attr(player, "display_name", "name", default=player_id))
        return player_id

    async def _spotify_connect_config(self) -> Any | None:
        try:
            configs = await self.client.config.get_provider_configs(
                provider_domain=SPOTIFY_CONNECT_DOMAIN, include_values=True
            )
        except TypeError:
            configs = [
                item
                for item in await self.client.config.get_provider_configs()
                if str(_attr(item, "domain")) == SPOTIFY_CONNECT_DOMAIN
            ]
        except Exception:  # noqa: BLE001
            return None
        return configs[0] if configs else None

    def _connected_player_ids(self, config: Any) -> list[str]:
        values = _attr(config, "values") or {}
        raw = None
        if isinstance(values, dict):
            entry = values.get(CONF_CONNECTED_PLAYERS)
            if hasattr(entry, "value"):
                raw = entry.value
            else:
                raw = entry
        if raw is None:
            return []
        if isinstance(raw, list):
            return [str(item) for item in raw]
        return [str(raw)]

    def _engine_from_config(self, config: Any) -> str | None:
        values = _attr(config, "values") or {}
        setup = _attr(config, "setup_data") or {}
        raw = None
        if isinstance(values, dict):
            entry = values.get(CONF_BACKEND)
            raw = entry.value if hasattr(entry, "value") else entry
        if raw is None and isinstance(setup, dict):
            raw = setup.get(CONF_BACKEND)
        if raw:
            return str(raw)
        return None

    async def spotify_connect_status(self) -> SpotifyConnectStatus:
        manifests = self._manifest_map()
        available = SPOTIFY_CONNECT_DOMAIN in manifests
        config = await self._spotify_connect_config()
        if config is None:
            return SpotifyConnectStatus(
                available=available,
                configured=False,
                note=None
                if available
                else "Spotify Connect is not present on this Music Assistant server.",
            )
        instance_id = str(_attr(config, "instance_id"))
        engine = self._engine_from_config(config)
        enabled_ids = set(self._connected_player_ids(config))
        rooms: list[SpotifyConnectRoom] = []
        try:
            players = list(self.client.players.players)
        except Exception:  # noqa: BLE001
            players = []
        for player in players:
            player_id = str(_attr(player, "player_id", "id", default=""))
            if not player_id:
                continue
            typ = str(_attr(player, "type", "player_type", default="") or "")
            # Include ordinary rooms and groups — both can be advertised in Spotify.
            rooms.append(
                SpotifyConnectRoom(
                    player_id=player_id,
                    name=str(_attr(player, "display_name", "name", default=player_id)),
                    enabled=player_id in enabled_ids if enabled_ids else False,
                )
            )
            del typ
        status = map_provider_status(_attr(config, "status"), available=True)
        note = None
        if engine == "go_librespot":
            note = (
                "This household uses go-librespot. Spotify accounts created after December 2024 "
                "may be limited. Spotify Soloist is the official alternative, set up in Music "
                "Assistant if you need it."
            )
        elif engine == "soloist":
            note = "This household uses Spotify Soloist, the official Connect engine."
        return SpotifyConnectStatus(
            available=True,
            configured=True,
            instance_id=instance_id,
            engine=engine,
            engine_label=_ENGINE_LABELS.get(engine or "", engine),
            status=status,
            rooms=rooms,
            note=note,
        )

    async def set_spotify_connect(self, player_id: str, enabled: bool) -> SpotifyConnectStatus:
        config = await self._spotify_connect_config()
        if config is None:
            raise MusicServiceError(
                404,
                "Spotify Connect is not set up on this household yet.",
            )
        instance_id = str(_attr(config, "instance_id"))
        current = self._connected_player_ids(config)
        ids = list(current)
        if enabled and player_id not in ids:
            ids.append(player_id)
        if not enabled:
            ids = [item for item in ids if item != player_id]
        try:
            await self.client.config.save_provider_config(
                provider_domain=SPOTIFY_CONNECT_DOMAIN,
                values={CONF_CONNECTED_PLAYERS: ids},
                instance_id=instance_id,
            )
        except MusicAssistantUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MusicServiceError(400, str(exc)) from exc
        return await self.spotify_connect_status()

