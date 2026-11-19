"""Fixtures: several in-process players wired together over a fake LAN."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from freesounderd.app import create_app
from freesounderd.config import DaemonSettings
from freesounderd.engine import Engine
from freesounderd.store import Store


class FakeSnapserver:
    """Enough of Snapcast's JSON-RPC for the engine's reconcile."""

    def __init__(self) -> None:
        self.clients: dict[str, dict[str, Any]] = {}
        self.groups: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.streams = [
            {"id": s, "status": "idle", "properties": {}}
            for s in ("Spotify", "AirPlay", "Radio", "Idle")
        ]

    def connect(self, cid: str) -> None:
        client = {
            "id": cid,
            "connected": True,
            "config": {"volume": {"percent": 100, "muted": False}},
        }
        self.clients[cid] = client
        self.groups.append({"id": f"g-{cid}", "stream_id": "Idle", "clients": [client]})

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        method = payload["method"]
        params = payload.get("params") or {}
        self.calls.append((method, params))
        result: Any = {}
        if method == "Server.GetStatus":
            result = {"server": {"groups": self.groups, "streams": self.streams}}
        elif method == "Client.SetVolume":
            self.clients[params["id"]]["config"]["volume"] = params["volume"]
        elif method == "Client.SetLatency":
            self.clients[params["id"]]["config"]["latency"] = params["latency"]
        elif method == "Group.SetStream":
            for group in self.groups:
                if group["id"] == params["id"]:
                    group["stream_id"] = params["stream_id"]
        elif method == "Group.SetClients":
            wanted = params["clients"]
            target = next(g for g in self.groups if g["id"] == params["id"])
            for group in self.groups:
                group["clients"] = [c for c in group["clients"] if c["id"] not in wanted]
            removed = [c for c in target["clients"]]
            target["clients"] = [self.clients[c] for c in wanted]
            for client in removed:
                self.groups.append(
                    {"id": f"g2-{client['id']}", "stream_id": "Idle", "clients": [client]}
                )
            self.groups = [g for g in self.groups if g["clients"]]
        return {"id": payload["id"], "jsonrpc": "2.0", "result": result}


class FakeMpd:
    """Enough of MPD for the engine: a queue, play state, volume, a tiny db."""

    def __init__(self) -> None:
        self.queue: list[str] = []
        self.state = "stop"
        self.pos = 0
        self.elapsed = 0.0
        self.volume = 50
        self.version = 1
        self.calls: list[list[Any]] = []
        self.db: list[dict[str, str]] = []
        self.pictures: dict[str, bytes] = {}
        self.fail_seek = False

    def _status(self) -> dict[str, str]:
        out = {
            "state": self.state,
            "playlist": str(self.version),
            "playlistlength": str(len(self.queue)),
            "volume": str(self.volume),
        }
        if self.state != "stop" and self.queue:
            out.update({"song": str(self.pos), "elapsed": f"{self.elapsed:.3f}"})
        return out

    async def status(self) -> dict[str, str]:
        return self._status()

    async def currentsong(self) -> dict[str, str]:
        if self.state == "stop" or not self.queue:
            return {}
        return {"file": self.queue[self.pos], "Pos": str(self.pos)}

    def external_play(self, url: str) -> None:
        """A UPnP controller (via upmpdcli) replaces the queue and plays."""

        self.queue = [url]
        self.version += 2
        self.pos, self.state, self.elapsed = 0, "play", 0.0

    def _run(self, cmd: list[Any]) -> list[tuple[str, str]]:
        from freesounderd.mpd import MpdError

        name, args = cmd[0], [str(a) for a in cmd[1:]]
        self.calls.append([name, *args])
        if name == "clear":
            self.queue, self.version = [], self.version + 1
        elif name == "add":
            self.queue.append(args[0])
            self.version += 1
        elif name == "play":
            if self.queue:
                self.pos = int(args[0]) if args else self.pos
                self.state, self.elapsed = "play", 0.0
        elif name == "seek":
            if self.fail_seek:
                raise MpdError("[2@0] {seek} not seekable")
            self.pos, self.elapsed, self.state = int(args[0]), float(args[1]), "play"
        elif name == "seekcur":
            self.elapsed = float(args[0])
        elif name == "pause":
            if self.state != "stop":
                self.state = "pause" if args and args[0] == "1" else "play"
        elif name == "stop":
            self.state = "stop"
        elif name == "next":
            if self.pos + 1 < len(self.queue):
                self.pos, self.elapsed = self.pos + 1, 0.0
            else:
                self.state = "stop"
        elif name == "previous":
            self.pos, self.elapsed = max(0, self.pos - 1), 0.0
        elif name == "setvol":
            self.volume = int(args[0])
        elif name == "stats":
            albums = {r.get("Album") for r in self.db}
            artists = {r.get("AlbumArtist") for r in self.db}
            return [
                ("artists", str(len(artists))),
                ("albums", str(len(albums))),
                ("songs", str(len(self.db))),
            ]
        elif name == "list" and args[0] == "albumartist":
            return [("AlbumArtist", a) for a in sorted({r["AlbumArtist"] for r in self.db})]
        elif name == "list" and args[0] == "album":
            if len(args) >= 3 and args[1] == "albumartist":
                return [
                    ("Album", a)
                    for a in sorted({r["Album"] for r in self.db if r["AlbumArtist"] == args[2]})
                ]
            out: list[tuple[str, str]] = []
            for artist in sorted({r["AlbumArtist"] for r in self.db}):
                out.append(("AlbumArtist", artist))
                out += [
                    ("Album", a)
                    for a in sorted({r["Album"] for r in self.db if r["AlbumArtist"] == artist})
                ]
            return out
        elif name == "find":
            want = dict(zip(args[0::2], args[1::2], strict=False))
            keys = {"album": "Album", "albumartist": "AlbumArtist"}
            hits = [r for r in self.db if all(r.get(keys[k]) == v for k, v in want.items())]
            return [kv for r in hits for kv in r.items()]
        elif name == "search":
            q = args[1].lower()
            hits = [r for r in self.db if any(q in v.lower() for v in r.values())]
            return [kv for r in hits for kv in r.items()]
        elif name == "lsinfo":
            path = args[0] if args else ""
            dirs, files = set(), []
            for r in self.db:
                f = r["file"]
                if path and not f.startswith(path + "/"):
                    continue
                rest = f[len(path) + 1 :] if path else f
                if "/" in rest:
                    dirs.add((path + "/" if path else "") + rest.split("/", 1)[0])
                else:
                    files.append(r)
            out = [("directory", d) for d in sorted(dirs)]
            return out + [kv for r in files for kv in r.items()]
        return []

    async def command(self, name: str, *args: Any, binary: bool = False) -> list[tuple[str, str]]:
        return self._run([name, *args])

    async def command_list(self, commands: list[list[Any]]) -> None:
        snapshot = (list(self.queue), self.state, self.pos, self.elapsed, self.version)
        try:
            for cmd in commands:
                self._run(cmd)
        except Exception:
            self.queue, self.state, self.pos, self.elapsed, self.version = snapshot
            raise

    async def binary(self, name: str, uri: str) -> tuple[bytes, str | None]:
        if name == "readpicture" and uri in self.pictures:
            return self.pictures[uri], "image/jpeg"
        return b"", None


class Lan:
    def __init__(self) -> None:
        self.apps: dict[str, Any] = {}
        self.snapservers: dict[str, FakeSnapserver] = {}
        self.spotify: dict[str, dict[str, Any] | None] = {}
        self.spotify_calls: list[tuple[str, str, Any]] = []
        self.itunes: list[dict[str, Any]] = []
        self.feeds: dict[str, bytes] = {}
        self.down: set[str] = set()

    def transport(self, local_ip: str) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            if host == "127.0.0.1":
                host = local_ip
            if host in self.down:
                raise httpx.ConnectError("down", request=request)
            port = request.url.port
            if port == 1780:
                server = self.snapservers.setdefault(host, FakeSnapserver())
                import json

                return httpx.Response(200, json=server.handle(json.loads(request.content)))
            if host == "itunes.apple.com":
                return httpx.Response(200, json={"results": self.itunes})
            if host in self.feeds:
                return httpx.Response(200, content=self.feeds[host])
            if port == 3678:
                import json

                status = self.spotify.get(host)
                if request.url.path == "/status":
                    return httpx.Response(200 if status else 204, json=status)
                body = json.loads(request.content) if request.content else None
                self.spotify_calls.append((host, request.url.path, body))
                if not status:
                    return httpx.Response(204)
                if request.url.path == "/context/tracks":
                    uri = request.url.params.get("uri")
                    return httpx.Response(200, json={"uri": uri, "ready": True, "tracks": []})
                return httpx.Response(200, json={})
            app = self.apps.get(host)
            if app is None:
                raise httpx.ConnectError("no route", request=request)
            transport = httpx.ASGITransport(app=app)
            await request.aread()
            return await transport.handle_async_request(request)

        return httpx.MockTransport(handler)


class Player:
    def __init__(self, lan: Lan, tmp: Path, ip: str, model: str = "Raspberry Pi 5") -> None:
        self.ip = ip
        root = tmp / ip
        self.settings = DaemonSettings(
            manage_system=False,
            mdns_enabled=False,
            advertise_ip=ip,
            config_dir=root / "etc",
            state_dir=root / "var",
            run_dir=root / "run",
            alsa_conf=root / "alsa.conf",
            camilla_conf=root / "camilla.yml",
            snapserver_conf=root / "snapserver.conf",
            snapclient_env=root / "snapclient.env",
            spotify_dir=root / "spotify",
            music_dir=root / "music",
            apply_command=None,
            ssh_command=None,
            airplay_version=2,
            poll_interval=0.05,
            adopt_wait=0.3,
        )
        self.store = Store(self.settings)
        self.engine = Engine(
            self.settings,
            self.store,
            http=httpx.AsyncClient(transport=lan.transport(ip)),
        )
        self.engine._model = model
        self.mpd = FakeMpd()
        self.engine.media.mpd = self.mpd
        self.engine.library.mpd = self.mpd
        self.upnp_meta: dict[str, Any] = {}

        async def renderer_track() -> dict[str, Any]:
            return dict(self.upnp_meta)

        self.engine.renderer.track = renderer_track  # type: ignore[method-assign]
        self.app = create_app(self.settings, self.store, engine=self.engine, run_loop=False)
        lan.apps[ip] = self.app
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=f"http://{ip}:8080"
        )

    @property
    def id(self) -> str:
        return self.engine.id


def introduce(players: list[Player]) -> None:
    """Stand-in for mDNS: every player knows every other one's address."""

    for p in players:
        for q in players:
            if p is not q:
                p.engine.peers.setdefault(q.id, {"id": q.id, "ip": q.ip, "port": 8080, "_seen": 0})


def wire_snapclients(lan: Lan, players: list[Player]) -> None:
    """Connect each player's snapclient to the server it was pointed at."""

    for p in players:
        target = p.engine._snap_target
        if not target:
            continue
        host = p.ip if target == "127.0.0.1" else target
        for ip, server in lan.snapservers.items():
            if ip != host and p.id in server.clients:
                server.clients.pop(p.id)
                for group in server.groups:
                    group["clients"] = [c for c in group["clients"] if c["id"] != p.id]
                server.groups = [g for g in server.groups if g["clients"]]
        server = lan.snapservers.setdefault(host, FakeSnapserver())
        if p.id not in server.clients:
            server.connect(p.id)


async def settle(lan: Lan, players: list[Player], rounds: int = 3) -> None:
    for _ in range(rounds):
        for p in players:
            await p.engine.tick()
        wire_snapclients(lan, players)


@pytest.fixture
def lan() -> Lan:
    return Lan()


@pytest.fixture
def make_player(lan: Lan, tmp_path: Path):
    def make(ip: str, model: str = "Raspberry Pi 5") -> Player:
        return Player(lan, tmp_path, ip, model)

    return make


def age_out(player: Player, peer_id: str, seconds: float = 60) -> None:
    rec = player.engine.peers[peer_id]
    rec["_seen"] = time.monotonic() - seconds
