"""dbus-next ServiceInterface properties must be constructible without a bus.

This is the regression test for the v0.4.1 Pi failure:

    property "Characteristics" is writable but does not have a setter

dbus-next defaults @dbus_property() to READWRITE and raises that ValueError
in ServiceInterface.__init__ if there is no setter. Instantiating every
GATT/advertisement interface on x86 (no BlueZ, no adapter) is enough to
catch the class of bug.
"""

from __future__ import annotations

import inspect

import pytest
from dbus_next.service import ServiceInterface

from freesounder_ble_provisioning.protocol import (
    CHAR_CAPABILITIES,
    DeviceIdentity,
    ImprovSession,
    NetworkPort,
)
from freesounder_ble_provisioning.status import _parse_busctl_variant, read_live_adapter


class _Net(NetworkPort):
    async def scan_wifi(self):  # noqa: ANN201
        return []

    async def connect_wifi(self, ssid: str, password: str):  # noqa: ANN201
        del ssid, password
        from freesounder_ble_provisioning.protocol import ConnectResult

        return ConnectResult(ok=False)

    def has_usable_network(self) -> bool:
        return False


def _session() -> ImprovSession:
    return ImprovSession(
        network=_Net(),
        identity=DeviceIdentity(
            firmware_name="FreeSounder",
            firmware_version="0.4.2",
            hardware="test",
            device_name="Test",
            player_id="player-test",
            hostname="fs-new",
        ),
    )


def _all_service_interface_classes() -> list[type[ServiceInterface]]:
    from freesounder_ble_provisioning import bluez

    classes: list[type[ServiceInterface]] = []
    for _name, obj in inspect.getmembers(bluez, inspect.isclass):
        if issubclass(obj, ServiceInterface) and obj is not ServiceInterface:
            classes.append(obj)
    assert classes, "no ServiceInterface classes found in bluez"
    return classes


def _construct(cls: type[ServiceInterface]) -> ServiceInterface:
    session = _session()
    name = cls.__name__
    if name == "ImprovApplication":
        return cls(session, bus=None)
    if name in {"ImprovAdvertisement", "ImprovAdvertisementPrimaryOnly"}:
        return cls(session)
    if name == "ImprovService":
        return cls()
    if name == "ImprovCommandCharacteristic":
        return cls(session)
    if name == "ImprovCharacteristic":
        return cls(CHAR_CAPABILITIES, "/org/freesounder/improv/service0", ["read"], lambda: b"\x00")
    # Unknown class: try no-arg, then session.
    try:
        return cls()
    except TypeError:
        return cls(session)


def test_every_service_interface_constructs_with_read_only_properties() -> None:
    found = []
    for cls in _all_service_interface_classes():
        instance = _construct(cls)
        found.append(cls.__name__)
        props = ServiceInterface._get_properties(instance)
        writable_without_setter = [
            prop.name
            for prop in props
            if prop.access.writable() and prop.prop_setter is None and not prop.disabled
        ]
        assert writable_without_setter == [], (
            f"{cls.__name__} has writable properties without a setter: "
            f"{writable_without_setter}"
        )
    # The GATT and advertisement interfaces that failed on v0.4.1 must be
    # in the set we actually constructed.
    for required in (
        "ImprovService",
        "ImprovCharacteristic",
        "ImprovCommandCharacteristic",
        "ImprovAdvertisement",
        "ImprovAdvertisementPrimaryOnly",
        "ImprovApplication",
    ):
        assert required in found


def test_characteristics_property_is_readable() -> None:
    from freesounder_ble_provisioning.bluez import ImprovService

    service = ImprovService()
    props = {p.name: p for p in ServiceInterface._get_properties(service)}
    assert "Characteristics" in props
    assert not props["Characteristics"].access.writable()
    assert props["Characteristics"].prop_setter is None
    assert service.Characteristics  # paths for the five Improv characteristics


def test_primary_only_advertisement_hides_scan_response_properties() -> None:
    from freesounder_ble_provisioning.bluez import (
        ImprovAdvertisement,
        ImprovAdvertisementPrimaryOnly,
    )

    full = ImprovAdvertisement(_session(), "FS-44BD")
    names = {p.name for p in ServiceInterface._get_properties(full) if not p.disabled}
    assert "ScanResponseManufacturerData" in names
    # BlueZ 5.82 rejects name AD types inside ScanResponseData; never expose it.
    assert "ScanResponseData" not in names
    assert "LocalName" in names
    assert full.LocalName == "FS-44BD"

    primary = ImprovAdvertisementPrimaryOnly(_session(), "FS-44BD")
    visible = {p.name for p in ServiceInterface._get_properties(primary) if not p.disabled}
    assert "ScanResponseManufacturerData" not in visible
    assert "LocalName" in visible
    assert "ServiceUUIDs" in visible
    assert primary.use_scan_response is False


def test_scan_response_name_fits_with_manufacturer_data() -> None:
    from freesounder_ble_provisioning.protocol import (
        MANUFACTURER_PAYLOAD,
        legacy_scan_response_length,
        scan_response_name,
    )

    assert scan_response_name("FreeSounder-44BD") == "FS-44BD"
    assert scan_response_name("weird") == "FS"
    size = legacy_scan_response_length(
        manufacturer_payload=MANUFACTURER_PAYLOAD, local_name="FS-44BD"
    )
    assert size == 24
    assert size <= 31


def test_parse_busctl_and_live_adapter(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _parse_busctl_variant("b true") is True
    assert _parse_busctl_variant("b false") is False
    assert _parse_busctl_variant('s "AA:BB:CC:DD:EE:FF"') == "AA:BB:CC:DD:EE:FF"

    hci = tmp_path / "hci0"
    hci.mkdir()
    (hci / "address").write_text("11:22:33:44:55:66\n", encoding="utf-8")

    def run(argv: list[str]) -> str:
        if argv[-1] == "Powered":
            return "b true"
        if argv[-1] == "Address":
            return 's "11:22:33:44:55:66"'
        return ""

    info = read_live_adapter(sys_class_bluetooth=tmp_path, run=run)
    assert info["address"] == "11:22:33:44:55:66"
    assert info["powered"] is True
    assert info["adapter"] == "hci0"
