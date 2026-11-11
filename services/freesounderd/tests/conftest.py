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


class Lan:
    def __init__(self) -> None:
        self.apps: dict[str, Any] = {}
        self.snapservers: dict[str, FakeSnapserver] = {}
        self.spotify: dict[str, dict[str, Any] | None] = {}
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
            if port == 3678:
                status = self.spotify.get(host)
                if request.url.path == "/status":
                    return httpx.Response(200 if status else 204, json=status)
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
            apply_command=None,
            ssh_command=None,
            airplay_version=2,
            poll_interval=0.05,
        )
        self.store = Store(self.settings)
        self.engine = Engine(
            self.settings,
            self.store,
            http=httpx.AsyncClient(transport=lan.transport(ip)),
        )
        self.engine._model = model
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
