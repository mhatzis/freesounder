"""FastAPI application: the v2 player API (see docs/architecture-v2.md)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from freesounderd import __version__, peers
from freesounderd.config import DaemonSettings, load_settings
from freesounderd.discovery import Discovery, local_ip
from freesounderd.engine import Engine, EngineError
from freesounderd.models import (
    AdoptBody,
    GroupMembersBody,
    Health,
    JoinBody,
    PlayerSettings,
    PlayerSettingsPatch,
    RadioPlayBody,
    SetupBody,
    SshDeveloperBody,
    SshDeveloperStatus,
    VolumeBody,
)
from freesounderd.ssh import ssh_set, ssh_status
from freesounderd.store import Store, detected_hat, hostname

logger = logging.getLogger(__name__)


def create_app(
    settings: DaemonSettings | None = None,
    store: Store | None = None,
    *,
    engine: Engine | None = None,
    enable_mdns: bool | None = None,
    run_loop: bool = True,
) -> FastAPI:
    settings = settings or load_settings()
    store = store or Store(settings)
    engine = engine or Engine(settings, store)
    should_advertise = settings.mdns_enabled if enable_mdns is None else enable_mdns

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        if should_advertise:
            discovery = Discovery(
                port=settings.port, txt=engine.txt, hostname=hostname, on_change=engine.kick
            )
            try:
                await discovery.start()
                engine.discovery = discovery
            except Exception:  # noqa: BLE001
                logger.warning("mDNS unavailable", exc_info=True)
        if run_loop:
            await engine.start()
        yield
        if engine.discovery is not None:
            with contextlib.suppress(Exception):
                await engine.discovery.stop()
        await engine.stop()

    app = FastAPI(title="freesounderd", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.state.engine = engine
    app.state.store = store

    @app.exception_handler(EngineError)
    async def _engine_error(_request: Request, exc: EngineError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    async def require_peer(request: Request) -> None:
        hh = store.household()
        body = await request.body()
        if hh is None or not peers.verify(
            hh.key, hh.id, request.method, request.url.path, body, dict(request.headers)
        ):
            raise HTTPException(status_code=403, detail="Not a member of this household")

    def info() -> dict[str, Any]:
        hh = store.household()
        settings_now = store.load_settings()
        group = engine.group_for_room(engine.id)
        return {
            "id": engine.id,
            "room": settings_now.name,
            "name": settings_now.name,
            "state": store.state(),
            "household": {"id": hh.id, "name": hh.name} if hh else None,
            "hostname": hostname(),
            "model": engine.self_record()["model"],
            "version": __version__,
            "api": "v2",
            "ip": settings.advertise_ip or local_ip(),
            "port": settings.port,
            "leader_id": engine.leader_id,
            "group_id": group["id"] if group else engine.id,
            "features": {
                "radio": True,
                "spotify_connect": True,
                "airplay": engine.airplay_version,
                "spotify_browse": False,
            },
            "dac": settings_now.dac,
            "hat": detected_hat(),
        }

    # -------------------------------------------------------------- basics
    @app.get("/health", response_model=Health)
    async def health() -> Health:
        return Health(version=__version__, state=store.state(), id=engine.id)  # type: ignore[arg-type]

    @app.get("/api/v2/info")
    async def get_info() -> dict[str, Any]:
        return info()

    @app.post("/api/v2/setup")
    async def setup(body: SetupBody, background: BackgroundTasks) -> dict[str, Any]:
        current = store.household()
        if current is not None and (body.household is None or body.household.id != current.id):
            raise HTTPException(status_code=409, detail="This player is already set up.")
        household = body.household or store.new_household()
        store.save_household(household)
        store.patch_settings(PlayerSettingsPatch(name=body.room.strip()))
        engine.kick()
        background.add_task(asyncio.to_thread, store.run_hook)
        result = info()
        result["room"] = body.room.strip()
        return result

    @app.post("/api/v2/reset")
    async def reset(body: dict[str, Any], background: BackgroundTasks) -> dict[str, Any]:
        if not body.get("confirm"):
            raise HTTPException(status_code=400, detail="Set confirm=true to reset this player.")
        store.clear_household()
        (settings.config_dir / "name").unlink(missing_ok=True)
        engine.group = store.load_group()
        engine.peers.clear()
        engine.kick()
        background.add_task(asyncio.to_thread, store.run_hook)
        return {"ok": True}

    @app.get("/api/v2/household")
    async def household() -> dict[str, Any]:
        hh = store.household()
        if hh is None:
            raise HTTPException(status_code=404, detail="Not set up yet")
        return {
            "id": hh.id,
            "name": hh.name,
            "rooms": engine.rooms(),
            "groups": engine._public_groups(),
        }

    # -------------------------------------------------------------- players
    @app.get("/api/v2/players/unclaimed")
    async def unclaimed() -> list[dict[str, Any]]:
        out = []
        if engine.discovery is not None:
            for found in engine.discovery.found.values():
                if found.txt.get("state") == "unclaimed" and found.id != engine.id:
                    out.append(
                        {
                            "id": found.id,
                            "name": found.txt.get("room") or found.name.split(".")[0],
                            "state": "unconfigured",
                            "model": found.txt.get("model"),
                            "ip": found.ip,
                            "url": f"http://{found.ip}:{found.port}",
                        }
                    )
        return out

    @app.post("/api/v2/players/adopt")
    async def adopt(body: AdoptBody) -> dict[str, Any]:
        hh = store.household()
        if hh is None:
            raise HTTPException(status_code=409, detail="Set this player up first.")
        url = body.url or (f"http://{body.ip}:8080" if body.ip else None)
        if not url:
            raise HTTPException(status_code=400, detail="Give the new player's url or ip.")
        try:
            res = await engine.http.post(
                url.rstrip("/") + "/api/v2/setup",
                json={"room": body.room, "household": hh.model_dump()},
                timeout=15,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502, detail=f"New player did not answer: {exc}"
            ) from exc
        if res.status_code >= 400:
            try:
                detail = res.json().get("detail")
            except ValueError:
                detail = res.text[:200]
            raise HTTPException(status_code=res.status_code, detail=detail)
        engine.kick()
        return res.json()

    # -------------------------------------------------------------- rooms & groups
    @app.get("/api/v2/rooms")
    async def rooms() -> list[dict[str, Any]]:
        return engine.rooms()

    @app.get("/api/v2/groups")
    async def groups() -> list[dict[str, Any]]:
        return engine._public_groups()

    @app.put("/api/v2/groups/{gid}/members")
    async def set_members(gid: str, body: GroupMembersBody) -> list[dict[str, Any]]:
        await engine.set_members(gid, body.members)
        return engine._public_groups()

    @app.post("/api/v2/rooms/{rid}/join")
    async def join(rid: str, body: JoinBody) -> dict[str, Any]:
        target = body.group or body.room
        if not target:
            raise HTTPException(status_code=400, detail="Give the group or room to join.")
        await engine.join(rid, target)
        return {"ok": True}

    @app.post("/api/v2/rooms/{rid}/leave")
    async def leave(rid: str) -> dict[str, Any]:
        await engine.leave(rid)
        return {"ok": True}

    @app.post("/api/v2/rooms/{rid}/volume")
    async def room_volume(rid: str, body: VolumeBody) -> dict[str, Any]:
        await engine.set_room_volume(rid, body.volume, body.muted)
        return {"ok": True}

    @app.post("/api/v2/groups/{gid}/volume")
    async def group_volume(gid: str, body: VolumeBody) -> dict[str, Any]:
        await engine.set_group_volume(gid, body.volume, body.muted)
        return {"ok": True}

    for action in ("play", "pause", "stop", "next", "previous", "toggle"):

        async def _control(gid: str, _action: str = action) -> dict[str, Any]:
            await engine.control(gid, _action)
            return {"ok": True}

        app.add_api_route(f"/api/v2/groups/{{gid}}/{action}", _control, methods=["POST"])

    @app.get("/api/v2/groups/{gid}/now-playing")
    async def group_now_playing(gid: str) -> dict[str, Any]:
        group = next((g for g in engine.groups() if g["id"] == gid), None)
        if group is None:
            raise HTTPException(status_code=404, detail="Unknown group")
        return engine._np_view(group, group["leader_id"])

    @app.get("/api/v2/now-playing")
    async def now_playing(player_id: str) -> dict[str, Any]:
        return engine.now_playing_for_room(player_id)

    # -------------------------------------------------------------- radio
    @app.get("/api/v2/radio/search")
    async def radio_search(
        q: str | None = None,
        country: str | None = None,
        tag: str | None = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        if not q and not country and not tag:
            country = _wifi_country(settings)
        try:
            return await engine.radio_browser.search(
                name=q, countrycode=country, tag=tag, limit=max(1, min(limit, 100))
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/v2/groups/{gid}/radio")
    async def play_radio(gid: str, body: RadioPlayBody) -> dict[str, Any]:
        return await engine.play_radio(gid, body.model_dump(exclude_none=True))

    @app.get("/api/v2/radio/favourites")
    async def radio_favourites() -> list[dict[str, Any]]:
        return store.radio_favourites()

    # -------------------------------------------------------------- settings
    @app.get("/api/v2/rooms/{rid}/settings", response_model=PlayerSettings)
    async def get_settings(rid: str) -> Any:
        if rid != engine.id:
            return await engine.forward(rid, "GET", f"/api/v2/rooms/{rid}/settings")
        return store.load_settings()

    @app.patch("/api/v2/rooms/{rid}/settings", response_model=PlayerSettings)
    async def patch_settings(
        rid: str, body: PlayerSettingsPatch, background: BackgroundTasks
    ) -> Any:
        if rid != engine.id:
            return await engine.forward(
                rid, "PATCH", f"/api/v2/rooms/{rid}/settings", body.model_dump(exclude_none=True)
            )
        result, name_changed, dac_changed = store.patch_settings(body)
        if name_changed or dac_changed:
            background.add_task(asyncio.to_thread, store.run_hook, dac_changed=dac_changed)
        engine.kick()
        return result

    @app.get("/api/v2/rooms/{rid}/developer/ssh", response_model=SshDeveloperStatus)
    async def get_ssh(rid: str) -> Any:
        if rid != engine.id:
            return await engine.forward(rid, "GET", f"/api/v2/rooms/{rid}/developer/ssh")
        return await asyncio.to_thread(ssh_status, settings.ssh_command, hostname())

    @app.post("/api/v2/rooms/{rid}/developer/ssh", response_model=SshDeveloperStatus)
    async def set_ssh(rid: str, body: SshDeveloperBody) -> Any:
        if rid != engine.id:
            return await engine.forward(
                rid, "POST", f"/api/v2/rooms/{rid}/developer/ssh", body.model_dump()
            )
        if body.enabled and not body.confirm:
            raise HTTPException(
                status_code=400,
                detail="Set confirm=true to enable developer SSH. The API has no authentication.",
            )
        return await asyncio.to_thread(
            ssh_set,
            settings.ssh_command,
            hostname(),
            enabled=body.enabled,
            public_key=body.public_key,
        )

    @app.get("/api/v2/doctor")
    async def doctor() -> dict[str, Any]:
        return await engine.doctor()

    # -------------------------------------------------------------- peers
    @app.get("/api/v2/peer/state")
    async def peer_state(request: Request) -> dict[str, Any]:
        await require_peer(request)
        return engine.self_record()

    @app.post("/api/v2/peer/command")
    async def peer_command(request: Request) -> dict[str, Any]:
        await require_peer(request)
        return await engine.handle_peer_command(await request.json())

    # -------------------------------------------------------------- events
    @app.websocket("/api/v2/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        queue = engine.subscribe()
        try:
            for event in engine.snapshot_events():
                await socket.send_text(event)
            while True:
                getter = asyncio.ensure_future(queue.get())
                receiver = asyncio.ensure_future(socket.receive_text())
                done, pending = await asyncio.wait(
                    {getter, receiver}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if receiver in done:
                    receiver.result()  # raises on disconnect; pings are ignored
                if getter in done:
                    await socket.send_text(getter.result())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            engine.unsubscribe(queue)

    return app


def _wifi_country(settings: DaemonSettings) -> str | None:
    try:
        value = (settings.config_dir / "wifi-country").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value.upper() or None
