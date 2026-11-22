"""A small fake Sonos household for tests: SOAP on port 1400."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from html import escape
from typing import Any

import httpx


class FakeSonos:
    def __init__(self, speakers: dict[str, tuple[str, str]]) -> None:
        # ip -> (uid, name)
        self.by_ip = {
            ip: {"uid": uid, "name": name, "volume": 20, "muted": False}
            for ip, (uid, name) in speakers.items()
        }
        self.by_uid = {v["uid"]: (ip, v) for ip, v in self.by_ip.items()}
        self.coord = {v["uid"]: v["uid"] for v in self.by_ip.values()}  # uid -> coordinator uid
        self.transport: dict[str, dict[str, Any]] = {
            v["uid"]: {"state": "STOPPED", "uri": "", "meta": "", "queue": [], "track": 0}
            for v in self.by_ip.values()
        }
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def topology(self) -> str:
        groups: dict[str, list[str]] = {}
        for uid, c in self.coord.items():
            groups.setdefault(c, []).append(uid)
        out = ["<ZoneGroupState><ZoneGroups>"]
        for c, members in groups.items():
            out.append(f'<ZoneGroup Coordinator="{c}" ID="{c}:1">')
            for uid in members:
                ip, sp = self.by_uid[uid]
                out.append(
                    f'<ZoneGroupMember UUID="{uid}" '
                    f'Location="http://{ip}:1400/xml/device_description.xml" '
                    f'ZoneName="{sp["name"]}"/>'
                )
            out.append("</ZoneGroup>")
        out.append("</ZoneGroups></ZoneGroupState>")
        return "".join(out)

    def _reply(self, action: str, service: str, values: dict[str, Any]) -> httpx.Response:
        urn = f"urn:schemas-upnp-org:service:{service}:1"
        inner = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in values.items())
        body = (
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
            f'<u:{action}Response xmlns:u="{urn}">{inner}</u:{action}Response>'
            "</s:Body></s:Envelope>"
        )
        return httpx.Response(200, text=body)

    def handle(self, request: httpx.Request) -> httpx.Response:
        ip = request.url.host
        sp = self.by_ip.get(ip)
        if sp is None:
            raise httpx.ConnectError("no sonos", request=request)
        soap = request.headers["SOAPACTION"].strip('"')
        urn, action = soap.split("#")
        service = urn.split(":")[-2]
        root = ET.fromstring(request.content)
        args = {}
        for el in root.iter():
            if el.tag.endswith(action):
                args = {c.tag: c.text or "" for c in el}
        self.calls.append((ip, action, args))
        uid = sp["uid"]
        t = self.transport[self.coord[uid]]
        if action == "GetZoneGroupState":
            return self._reply(action, service, {"ZoneGroupState": self.topology()})
        if action == "GetVolume":
            return self._reply(action, service, {"CurrentVolume": sp["volume"]})
        if action == "SetVolume":
            sp["volume"] = int(args["DesiredVolume"])
        elif action == "GetMute":
            return self._reply(action, service, {"CurrentMute": 1 if sp["muted"] else 0})
        elif action == "SetMute":
            sp["muted"] = args["DesiredMute"] == "1"
        elif action == "GetTransportInfo":
            return self._reply(action, service, {"CurrentTransportState": t["state"]})
        elif action == "GetPositionInfo":
            meta = (
                t["queue"][t["track"]]["meta"]
                if t["queue"] and t["uri"].startswith("x-rincon-queue")
                else t["meta"]
            )
            return self._reply(
                action,
                service,
                {
                    "TrackMetaData": meta,
                    "TrackURI": t["uri"],
                    "RelTime": "0:00:05",
                    "TrackDuration": "0:03:00",
                },
            )
        elif action == "GetMediaInfo":
            return self._reply(action, service, {"CurrentURIMetaData": t["meta"]})
        elif action == "Play":
            t["state"] = "PLAYING"
        elif action == "Pause":
            t["state"] = "PAUSED_PLAYBACK"
        elif action == "Stop":
            t["state"] = "STOPPED"
        elif action == "SetAVTransportURI":
            uri = args["CurrentURI"]
            if uri.startswith("x-rincon:"):
                self.coord[uid] = uri.removeprefix("x-rincon:")
            else:
                own = self.transport[uid]
                own.update(uri=uri, meta=args.get("CurrentURIMetaData", ""))
        elif action == "BecomeCoordinatorOfStandaloneGroup":
            self.coord[uid] = uid
        elif action == "RemoveAllTracksFromQueue":
            self.transport[uid]["queue"] = []
        elif action == "AddURIToQueue":
            self.transport[uid]["queue"].append(
                {"uri": args["EnqueuedURI"], "meta": args["EnqueuedURIMetaData"]}
            )
        elif action == "Seek" and args.get("Unit") == "TRACK_NR":
            self.transport[uid]["track"] = int(args["Target"]) - 1
        return self._reply(action, service, {})
