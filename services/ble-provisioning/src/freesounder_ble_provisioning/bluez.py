"""BlueZ GATT peripheral for the Improv service, via dbus-next.

This process is a BLE peripheral only. It does not register audio profiles.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from freesounder_ble_provisioning.protocol import (
    ADVERTISEMENT_SERVICE_DATA_UUID,
    CHAR_CAPABILITIES,
    CHAR_CURRENT_STATE,
    CHAR_ERROR_STATE,
    CHAR_RPC_COMMAND,
    CHAR_RPC_RESULT,
    SERVICE_UUID,
    ImprovSession,
)
from freesounder_ble_provisioning.service import PeripheralHandle

logger = logging.getLogger(__name__)

BLUEZ = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
GATT_MANAGER = "org.bluez.GattManager1"
LE_ADVERTISING_MANAGER = "org.bluez.LEAdvertisingManager1"
DBUS_OM = "org.freedesktop.DBus.ObjectManager"
DBUS_PROPS = "org.freedesktop.DBus.Properties"

APP_PATH = "/org/freesounder/improv"
ADV_PATH = "/org/freesounder/improv/advertisement"
SVC_PATH = "/org/freesounder/improv/service0"


class BlueZPeripheral(PeripheralHandle):
    """Register an Improv GATT application and start advertising."""

    def __init__(self) -> None:
        self._bus: Any = None
        self._app: Any = None
        self._advertisement: Any = None
        self._adapter_path: str | None = None
        self._session: ImprovSession | None = None
        self._started = False

    async def start(self, session: ImprovSession, local_name: str) -> None:
        if self._started:
            await self.stop()
        self._session = session
        await _rfkill_unblock()
        from dbus_next.aio import MessageBus
        from dbus_next.constants import BusType

        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self._adapter_path = await _find_adapter(self._bus)
        if not self._adapter_path:
            raise RuntimeError("No BlueZ adapter found")
        await _prepare_adapter(self._bus, self._adapter_path, local_name)

        self._app = ImprovApplication(session, self._bus)
        self._advertisement = ImprovAdvertisement(session, local_name)
        self._bus.export(APP_PATH, self._app)
        for path, obj in self._app.objects():
            self._bus.export(path, obj)
        self._bus.export(ADV_PATH, self._advertisement)

        introspect = await self._bus.introspect(BLUEZ, self._adapter_path)
        proxy = self._bus.get_proxy_object(BLUEZ, self._adapter_path, introspect)
        gatt = proxy.get_interface(GATT_MANAGER)
        adv_mgr = proxy.get_interface(LE_ADVERTISING_MANAGER)
        await gatt.call_register_application(APP_PATH, {})  # type: ignore[union-attr]
        await adv_mgr.call_register_advertisement(ADV_PATH, {})  # type: ignore[union-attr]
        self._started = True
        logger.info("BlueZ Improv advertisement registered as %s", local_name)

    async def stop(self) -> None:
        if not self._started or self._bus is None or self._adapter_path is None:
            self._started = False
            return
        try:
            introspect = await self._bus.introspect(BLUEZ, self._adapter_path)
            proxy = self._bus.get_proxy_object(BLUEZ, self._adapter_path, introspect)
            try:
                adv_mgr = proxy.get_interface(LE_ADVERTISING_MANAGER)
                await adv_mgr.call_unregister_advertisement(ADV_PATH)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                logger.debug("unregister advertisement failed", exc_info=True)
            try:
                gatt = proxy.get_interface(GATT_MANAGER)
                await gatt.call_unregister_application(APP_PATH)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                logger.debug("unregister application failed", exc_info=True)
        finally:
            try:
                self._bus.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._bus = None
            self._started = False

    async def notify(self, session: ImprovSession) -> None:
        self._session = session
        if self._app is not None:
            await self._app.notify(session)
        if self._advertisement is not None:
            self._advertisement.update_service_data(session)


# D-Bus object implementations are imported lazily so unit tests do not need
# dbus-next at collection time if they never start a peripheral. The classes
# below are defined when this module is imported (packaging always has dbus-next).

from dbus_next import Variant  # noqa: E402
from dbus_next.service import ServiceInterface, dbus_property, method, signal  # noqa: E402


class ImprovApplication(ServiceInterface):
    """ObjectManager root for the GATT application."""

    def __init__(self, session: ImprovSession, bus: Any) -> None:
        super().__init__(DBUS_OM)
        self._bus = bus
        self.service = ImprovService()
        self.chars = {
            CHAR_CURRENT_STATE: ImprovCharacteristic(
                CHAR_CURRENT_STATE,
                SVC_PATH,
                ["read", "notify"],
                lambda: session.current_state_bytes(),
            ),
            CHAR_ERROR_STATE: ImprovCharacteristic(
                CHAR_ERROR_STATE,
                SVC_PATH,
                ["read", "notify"],
                lambda: session.error_state_bytes(),
            ),
            CHAR_RPC_RESULT: ImprovCharacteristic(
                CHAR_RPC_RESULT,
                SVC_PATH,
                ["read", "notify"],
                lambda: session.rpc_result,
            ),
            CHAR_CAPABILITIES: ImprovCharacteristic(
                CHAR_CAPABILITIES,
                SVC_PATH,
                ["read"],
                lambda: session.capabilities_bytes(),
            ),
            CHAR_RPC_COMMAND: ImprovCommandCharacteristic(session),
        }

    def objects(self) -> list[tuple[str, ServiceInterface]]:
        items: list[tuple[str, ServiceInterface]] = [(SVC_PATH, self.service)]
        for uuid, char in self.chars.items():
            items.append((_char_path(uuid), char))
        return items

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":  # type: ignore[name-defined]
        result: dict[str, dict[str, dict[str, Variant]]] = {}
        result[SVC_PATH] = self.service.as_dict()
        for uuid, char in self.chars.items():
            result[_char_path(uuid)] = char.as_dict()
        return result

    async def notify(self, session: ImprovSession) -> None:
        self.chars[CHAR_CURRENT_STATE].set_value(session.current_state_bytes())
        self.chars[CHAR_ERROR_STATE].set_value(session.error_state_bytes())
        self.chars[CHAR_RPC_RESULT].set_value(session.rpc_result)


class ImprovService(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.bluez.GattService1")

    @dbus_property()
    def UUID(self) -> "s":  # type: ignore[name-defined]
        return SERVICE_UUID

    @dbus_property()
    def Primary(self) -> "b":  # type: ignore[name-defined]
        return True

    @dbus_property()
    def Characteristics(self) -> "ao":  # type: ignore[name-defined]
        return [_char_path(u) for u in _CHAR_ORDER]

    def as_dict(self) -> dict[str, dict[str, Variant]]:
        return {
            "org.bluez.GattService1": {
                "UUID": Variant("s", SERVICE_UUID),
                "Primary": Variant("b", True),
                "Characteristics": Variant("ao", [_char_path(u) for u in _CHAR_ORDER]),
            }
        }


class ImprovCharacteristic(ServiceInterface):
    def __init__(
        self,
        uuid: str,
        service: str,
        flags: list[str],
        reader: Any,
    ) -> None:
        super().__init__("org.bluez.GattCharacteristic1")
        self._uuid = uuid
        self._service = service
        self._flags = flags
        self._reader = reader
        self._value = reader() if reader else b""

    def set_value(self, value: bytes) -> None:
        self._value = value
        if "notify" in self._flags:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", bytes(value))},
                [],
            )

    @signal()
    def PropertiesChanged(self, interface: "s", changed: "a{sv}", invalidated: "as") -> None:  # type: ignore[name-defined]
        return

    @dbus_property()
    def UUID(self) -> "s":  # type: ignore[name-defined]
        return self._uuid

    @dbus_property()
    def Service(self) -> "o":  # type: ignore[name-defined]
        return self._service

    @dbus_property()
    def Flags(self) -> "as":  # type: ignore[name-defined]
        return self._flags

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":  # type: ignore[name-defined]
        del options
        if self._reader:
            self._value = self._reader()
        return bytes(self._value)

    def as_dict(self) -> dict[str, dict[str, Variant]]:
        return {
            "org.bluez.GattCharacteristic1": {
                "UUID": Variant("s", self._uuid),
                "Service": Variant("o", self._service),
                "Flags": Variant("as", self._flags),
            }
        }


class ImprovCommandCharacteristic(ImprovCharacteristic):
    def __init__(self, session: ImprovSession) -> None:
        super().__init__(CHAR_RPC_COMMAND, SVC_PATH, ["write", "write-without-response"], lambda: b"")
        self._session = session

    @method()
    def WriteValue(self, value: "ay", options: "a{sv}") -> None:  # type: ignore[name-defined]
        del options
        # Schedule onto the running loop; never log `value` (may contain a PSK).
        loop = asyncio.get_event_loop()
        loop.create_task(self._session.push_rpc_bytes(bytes(value)))


class ImprovAdvertisement(ServiceInterface):
    def __init__(self, session: ImprovSession, local_name: str) -> None:
        super().__init__("org.bluez.LEAdvertisement1")
        self._session = session
        self._local_name = local_name
        self._service_data = session.service_data()

    def update_service_data(self, session: ImprovSession) -> None:
        self._session = session
        self._service_data = session.service_data()
        self.PropertiesChanged(
            "org.bluez.LEAdvertisement1",
            {"ServiceData": Variant("a{sv}", self._service_data_dict())},
            [],
        )

    def _service_data_dict(self) -> dict[str, Variant]:
        return {ADVERTISEMENT_SERVICE_DATA_UUID: Variant("ay", bytes(self._service_data))}

    @signal()
    def PropertiesChanged(self, interface: "s", changed: "a{sv}", invalidated: "as") -> None:  # type: ignore[name-defined]
        return

    @dbus_property()
    def Type(self) -> "s":  # type: ignore[name-defined]
        return "peripheral"

    @dbus_property()
    def LocalName(self) -> "s":  # type: ignore[name-defined]
        return self._local_name

    @dbus_property()
    def ServiceUUIDs(self) -> "as":  # type: ignore[name-defined]
        return [SERVICE_UUID]

    @dbus_property()
    def ServiceData(self) -> "a{sv}":  # type: ignore[name-defined]
        return self._service_data_dict()

    @dbus_property()
    def Includes(self) -> "as":  # type: ignore[name-defined]
        return []

    @method()
    def Release(self) -> None:
        return


_CHAR_ORDER = (
    CHAR_CURRENT_STATE,
    CHAR_ERROR_STATE,
    CHAR_RPC_COMMAND,
    CHAR_RPC_RESULT,
    CHAR_CAPABILITIES,
)


def _char_path(uuid: str) -> str:
    suffix = uuid.split("-")[-1]
    return f"{SVC_PATH}/char_{suffix}"


async def _find_adapter(bus: Any) -> str | None:
    introspect = await bus.introspect(BLUEZ, "/")
    obj = bus.get_proxy_object(BLUEZ, "/", introspect)
    manager = obj.get_interface(DBUS_OM)
    objects = await manager.call_get_managed_objects()
    for path, ifaces in objects.items():
        if ADAPTER_IFACE in ifaces:
            return path
    return None


async def _prepare_adapter(bus: Any, adapter_path: str, local_name: str) -> None:
    introspect = await bus.introspect(BLUEZ, adapter_path)
    proxy = bus.get_proxy_object(BLUEZ, adapter_path, introspect)
    props = proxy.get_interface(DBUS_PROPS)
    await props.call_set(ADAPTER_IFACE, "Powered", Variant("b", True))
    await props.call_set(ADAPTER_IFACE, "Pairable", Variant("b", False))
    try:
        await props.call_set(ADAPTER_IFACE, "Alias", Variant("s", local_name))
    except Exception:  # noqa: BLE001
        logger.debug("Could not set adapter alias", exc_info=True)
    try:
        await props.call_set(ADAPTER_IFACE, "Discoverable", Variant("b", True))
    except Exception:  # noqa: BLE001
        logger.debug("Could not set discoverable", exc_info=True)


async def _rfkill_unblock() -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "rfkill",
            "unblock",
            "bluetooth",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except OSError:
        pass
