"""In-memory Music Assistant stand-in for tests and UI mock mode."""

from __future__ import annotations

from freesounder_control_api.models import (
    AddMusicServiceBody,
    AuthSession,
    BrowseResponse,
    FormField,
    MusicService,
    MusicServicesCatalog,
    MusicServiceType,
    NowPlaying,
    Room,
    SearchItem,
    SearchResponse,
    SpotifyConnectRoom,
    SpotifyConnectStatus,
    StartAuthBody,
    SubmitAuthBody,
    UpdateMusicServiceBody,
)
from freesounder_control_api.music_catalog import (
    CALLBACK_PATH,
    CALLBACK_SCHEME,
    CURATED_SERVICES,
    account_from_fields,
    catalog_item_for,
    visible_values,
)
from freesounder_control_api.music_sessions import AuthStore, MusicServiceError, new_session_id


class MockMusicBackend:
    """Two rooms and optional groups, no network required."""

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {
            "kitchen": Room(
                id="kitchen",
                name="Kitchen",
                available=True,
                volume=40,
                muted=False,
                state="playing",
            ),
            "lounge": Room(
                id="lounge",
                name="Lounge",
                available=True,
                volume=30,
                muted=False,
                state="idle",
            ),
        }
        self.now: dict[str, NowPlaying] = {
            "kitchen": NowPlaying(
                player_id="kitchen",
                title="Example Track",
                artist="Example Artist",
                album="Example Album",
                state="playing",
                position=42,
                duration=240,
            ),
            "lounge": NowPlaying(player_id="lounge", state="idle"),
        }
        self.connected = True
        self._group_seq = 0
        self._listener = None
        self._auth = AuthStore()
        self._service_seq = 0
        self.services: dict[str, MusicService] = {
            "spotify-demo": MusicService(
                instance_id="spotify-demo",
                domain="spotify",
                name="Spotify",
                type="music",
                status="loaded",
                available=True,
                enabled=True,
                account="demo@example.com",
                auth_kind="oauth",
                can_reauthenticate=True,
                values={"username": "demo@example.com"},
                entries=[
                    FormField(
                        key="username",
                        type="string",
                        label="Account",
                        value="demo@example.com",
                        has_value=True,
                        read_only=True,
                    )
                ],
            )
        }
        self._spotify_connect_engine = "go_librespot"
        self._spotify_connect_rooms: dict[str, bool] = {
            "kitchen": True,
            "lounge": False,
        }
        self._qobuz_entries = [
            FormField(key="username", type="string", label="Username", required=True),
            FormField(
                key="password",
                type="secure_string",
                label="Password",
                required=True,
                secret=True,
            ),
        ]

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def health_connected(self) -> bool:
        return self.connected

    async def list_rooms(self) -> list[Room]:
        return list(self.rooms.values())

    def snapshot_rooms(self) -> list[Room]:
        return list(self.rooms.values())

    async def create_group(
        self,
        name: str,
        member_ids: list[str],
        provider: str,
        dynamic: bool | None,
    ) -> Room:
        del provider, dynamic
        self._group_seq += 1
        group_id = f"group-{self._group_seq}"
        members = [mid for mid in member_ids if mid in self.rooms]
        group = Room(
            id=group_id,
            name=name,
            available=True,
            volume=40,
            is_group=True,
            member_ids=members,
            group_id=group_id,
            state="idle",
        )
        self.rooms[group_id] = group
        for mid in members:
            room = self.rooms[mid]
            self.rooms[mid] = room.model_copy(update={"group_id": group_id})
        self.now[group_id] = NowPlaying(player_id=group_id, state="idle")
        return group

    async def join_group(self, group_id: str, player_id: str) -> None:
        group = self.rooms[group_id]
        members = list(group.member_ids)
        if player_id not in members:
            members.append(player_id)
        self.rooms[group_id] = group.model_copy(update={"member_ids": members, "is_group": True})
        if player_id in self.rooms:
            self.rooms[player_id] = self.rooms[player_id].model_copy(update={"group_id": group_id})

    async def leave_group(self, group_id: str, player_id: str) -> None:
        if group_id in self.rooms:
            group = self.rooms[group_id]
            members = [m for m in group.member_ids if m != player_id]
            self.rooms[group_id] = group.model_copy(update={"member_ids": members})
        if player_id in self.rooms:
            self.rooms[player_id] = self.rooms[player_id].model_copy(update={"group_id": None})

    async def now_playing(self, player_id: str) -> NowPlaying:
        if player_id in self.now:
            return self.now[player_id]
        return NowPlaying(player_id=player_id, state="idle")

    async def play(self, player_id: str) -> None:
        current = await self.now_playing(player_id)
        self.now[player_id] = current.model_copy(update={"state": "playing"})
        if player_id in self.rooms:
            self.rooms[player_id] = self.rooms[player_id].model_copy(update={"state": "playing"})

    async def pause(self, player_id: str) -> None:
        current = await self.now_playing(player_id)
        self.now[player_id] = current.model_copy(update={"state": "paused"})
        if player_id in self.rooms:
            self.rooms[player_id] = self.rooms[player_id].model_copy(update={"state": "paused"})

    async def next_track(self, player_id: str) -> None:
        current = await self.now_playing(player_id)
        title = current.title or "Example Track"
        self.now[player_id] = current.model_copy(
            update={"title": f"{title} (next)", "state": "playing"}
        )

    async def set_volume(self, player_id: str, level: int) -> None:
        if player_id in self.rooms:
            self.rooms[player_id] = self.rooms[player_id].model_copy(update={"volume": level})

    async def set_group_volume(self, group_id: str, level: int) -> None:
        await self.set_volume(group_id, level)
        group = self.rooms.get(group_id)
        if group:
            for mid in group.member_ids:
                await self.set_volume(mid, level)

    async def search(self, query: str, limit: int = 50) -> SearchResponse:
        del limit
        item = SearchItem(
            uri="library://track/example",
            name=f"Result for {query}",
            media_type="track",
            raw={"uri": "library://track/example", "name": f"Result for {query}"},
        )
        return SearchResponse(query=query, items=[item])

    def add_room(self, room_id: str, name: str) -> Room:
        room = Room(id=room_id, name=name, available=True, volume=30, muted=False, state="idle")
        self.rooms[room_id] = room
        self.now[room_id] = NowPlaying(player_id=room_id, state="idle")
        return room

    def rename_room(self, room_id: str, name: str) -> None:
        if room_id in self.rooms:
            self.rooms[room_id] = self.rooms[room_id].model_copy(update={"name": name})

    async def browse(self, path: str | None) -> BrowseResponse:
        item = SearchItem(
            uri="library://album/example",
            name="Example Album",
            media_type="album",
            raw={"path": path, "name": "Example Album"},
        )
        return BrowseResponse(path=path, items=[item])


    def bind_listener(self, listener) -> None:
        self._listener = listener

    async def _emit(self, event: str, data) -> None:
        if self._listener is not None:
            await self._listener(event, data)

    def _types(self) -> list[MusicServiceType]:
        configured_domains = {service.domain for service in self.services.values()}
        items = []
        for item in CURATED_SERVICES:
            # NFS is listed for completeness but this mock (and many MA builds) lack it.
            available = item.domain != "filesystem_nfs"
            items.append(
                catalog_item_for(
                    item.domain,
                    available_on_server=available,
                    ma_stage="experimental" if item.limited else "stable",
                    configured=item.domain in configured_domains and not item.multi_instance,
                    multi_instance=item.multi_instance,
                )
            )
        return items

    async def list_music_service_types(self) -> list[MusicServiceType]:
        return self._types()

    async def catalog_music_services(self) -> MusicServicesCatalog:
        return MusicServicesCatalog(
            services=await self.list_music_service_types(),
            configured=await self.list_music_services(),
        )

    async def list_music_services(self) -> list[MusicService]:
        return list(self.services.values())

    async def get_music_service(self, instance_id: str) -> MusicService:
        service = self.services.get(instance_id)
        if service is None:
            raise MusicServiceError(404, f"Music service not found: {instance_id}")
        return service

    def _add_service(self, domain: str, values: dict, name: str | None = None) -> MusicService:
        curated = catalog_item_for(domain, available_on_server=True)
        if domain == "filesystem_nfs":
            raise MusicServiceError(
                400,
                "NFS shares are not available in this Music Assistant version. "
                "Use an SMB share or a local folder on the host.",
            )
        if not curated.multi_instance:
            existing = next((s for s in self.services.values() if s.domain == domain), None)
            if existing is not None:
                raise MusicServiceError(409, f"{curated.name} is already connected")
        self._service_seq += 1
        instance_id = f"{domain}-{self._service_seq}"
        entries = [
            FormField(
                key=key,
                type=(
                    "secure_string"
                    if "pass" in key or "token" in key or "secret" in key
                    else "string"
                ),
                label=key.replace("_", " ").title(),
                value=None if ("pass" in key or "token" in key or "secret" in key) else value,
                has_value=value not in (None, ""),
                secret="pass" in key or "token" in key or "secret" in key,
            )
            for key, value in values.items()
        ]
        if domain == "qobuz" and not entries:
            entries = list(self._qobuz_entries)
        service = MusicService(
            instance_id=instance_id,
            domain=domain,
            name=name or curated.name,
            type="music",
            status="loaded",
            available=True,
            enabled=True,
            account=account_from_fields(entries) or values.get("username"),
            limited=curated.limited,
            limitation=curated.limitation,
            auth_kind=curated.auth_kind,
            can_reauthenticate=curated.auth_kind == "oauth",
            values=visible_values(entries),
            entries=entries,
        )
        self.services[instance_id] = service
        return service

    async def add_music_service(self, body: AddMusicServiceBody) -> AuthSession | MusicService:
        curated = catalog_item_for(body.domain, available_on_server=body.domain != "filesystem_nfs")
        if curated.auth_kind == "oauth" and not body.values:
            return await self.start_music_service_auth(StartAuthBody(domain=body.domain))
        if curated.auth_kind in {"credentials", "path"} and not body.values:
            session = AuthSession(
                session_id=new_session_id(),
                status="awaiting_form",
                domain=body.domain,
                entries=list(self._qobuz_entries)
                if body.domain
                in {
                    "qobuz",
                    "ytmusic",
                    "soundcloud",
                    "plex",
                    "jellyfin",
                    "opensubsonic",
                    "filesystem_smb",
                }
                else [
                    FormField(
                        key="path",
                        type="string",
                        label="Folder or share path",
                        required=True,
                    )
                ],
            )
            self._auth.put(session)
            return session
        service = self._add_service(body.domain, body.values, body.name)
        await self._emit("music_services", [item.model_dump() for item in self.services.values()])
        return service

    async def update_music_service(
        self, instance_id: str, body: UpdateMusicServiceBody
    ) -> MusicService:
        service = await self.get_music_service(instance_id)
        updates: dict = {}
        if body.name is not None:
            updates["name"] = body.name
        if body.enabled is not None:
            updates["enabled"] = body.enabled
            updates["status"] = "loaded" if body.enabled else "disabled"
        if body.values:
            values = dict(service.values)
            entries = list(service.entries)
            for key, value in body.values.items():
                if value in (None, "") and any(e.key == key and e.secret for e in entries):
                    continue
                values[key] = value
                found = False
                new_entries = []
                for entry in entries:
                    if entry.key == key:
                        found = True
                        new_entries.append(
                            entry.model_copy(
                                update={
                                    "value": None if entry.secret else value,
                                    "has_value": value not in (None, ""),
                                }
                            )
                        )
                    else:
                        new_entries.append(entry)
                if not found:
                    new_entries.append(
                        FormField(key=key, type="string", value=value, has_value=True)
                    )
                entries = new_entries
            secret_keys = {"password", "token", "secret"}
            updates["values"] = {k: v for k, v in values.items() if k not in secret_keys}
            updates["entries"] = entries
            updates["account"] = account_from_fields(entries) or service.account
        service = service.model_copy(update=updates)
        self.services[instance_id] = service
        await self._emit("music_services", [item.model_dump() for item in self.services.values()])
        return service

    async def remove_music_service(self, instance_id: str) -> None:
        if instance_id not in self.services:
            raise MusicServiceError(404, f"Music service not found: {instance_id}")
        del self.services[instance_id]
        await self._emit("music_services", [item.model_dump() for item in self.services.values()])

    async def start_music_service_auth(self, body: StartAuthBody) -> AuthSession:
        domain = body.domain
        instance_id = body.instance_id
        if instance_id:
            service = await self.get_music_service(instance_id)
            domain = service.domain
        if not domain:
            raise MusicServiceError(422, "Provide a service domain or instance id")
        session_id = new_session_id()
        session = AuthSession(
            session_id=session_id,
            status="awaiting_browser",
            domain=domain,
            instance_id=instance_id,
            auth_url=f"http://127.0.0.1:8080/api/v1/music-services/auth/{session_id}/callback?demo=1",
            callback_scheme=CALLBACK_SCHEME,
            callback_url=f"{CALLBACK_SCHEME}://{CALLBACK_PATH}",
            title=f"Sign in to {catalog_item_for(domain).name}",
            description="A browser window will open so you can sign in.",
        )
        self._auth.put(session)
        return session

    async def get_music_service_auth(self, session_id: str) -> AuthSession:
        session = self._auth.get(session_id)
        if session is None:
            raise MusicServiceError(404, f"Auth session not found: {session_id}")
        return session

    async def submit_music_service_auth(
        self, session_id: str, body: SubmitAuthBody
    ) -> AuthSession:
        session = await self.get_music_service_auth(session_id)
        service = self._add_service(session.domain, body.values)
        session = session.model_copy(
            update={"status": "success", "instance_id": service.instance_id, "entries": []}
        )
        self._auth.put(session)
        await self._emit("music_service_auth", session.model_dump())
        await self._emit("music_services", [item.model_dump() for item in self.services.values()])
        return session

    async def abort_music_service_auth(self, session_id: str) -> AuthSession:
        session = await self.get_music_service_auth(session_id)
        session = session.model_copy(update={"status": "aborted", "error": "Cancelled"})
        self._auth.put(session)
        await self._emit("music_service_auth", session.model_dump())
        return session

    async def complete_music_service_auth_callback(
        self, session_id: str, params: dict[str, str]
    ) -> AuthSession:
        del params
        session = await self.get_music_service_auth(session_id)
        if session.status == "success":
            return session
        service = self._add_service(session.domain, {"username": "demo@example.com"})
        session = session.model_copy(
            update={"status": "success", "instance_id": service.instance_id, "auth_url": None}
        )
        self._auth.put(session)
        await self._emit("music_service_auth", session.model_dump())
        await self._emit("music_services", [item.model_dump() for item in self.services.values()])
        return session

    async def spotify_connect_status(self) -> SpotifyConnectStatus:
        rooms = []
        for room in self.rooms.values():
            if room.is_group:
                continue
            rooms.append(
                SpotifyConnectRoom(
                    player_id=room.id,
                    name=room.name,
                    enabled=self._spotify_connect_rooms.get(room.id, False),
                )
            )
        return SpotifyConnectStatus(
            available=True,
            configured=True,
            instance_id="spotify-connect-demo",
            engine=self._spotify_connect_engine,
            engine_label="go-librespot",
            status="loaded",
            rooms=rooms,
            note="Demo household: Spotify Connect status is simulated.",
        )

    async def set_spotify_connect(self, player_id: str, enabled: bool) -> SpotifyConnectStatus:
        if player_id not in self.rooms:
            raise MusicServiceError(404, f"Room not found: {player_id}")
        self._spotify_connect_rooms[player_id] = enabled
        return await self.spotify_connect_status()
