"""BlueZ GATT peripheral for the Improv service, via dbus-next.

This process is a BLE peripheral only. It does not register audio profiles.
"""

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
    COMPANY_ID_TEST,
    MANUFACTURER_PAYLOAD,
    SERVICE_UUID,
    SHORT_LOCAL_NAME,
    ImprovSession,
    legacy_advertisement_length,
    legacy_scan_response_length,
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
        if self._bus is not None:
            await self.stop()
        self._session = session
        from dbus_next.aio import MessageBus
        from dbus_next.constants import BusType

        await _rfkill_unblock()
        await _try_bring_up_controller()
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        try:
            self._adapter_path = await _wait_for_adapter(self._bus)
            await _prepare_adapter(self._bus, self._adapter_path, local_name)

            self._app = ImprovApplication(session, self._bus)
            # Do not put LocalName in the primary PDU: flags + 128-bit UUID +
            # Improv service data already use all 31 legacy advertising bytes.
            # The adapter Alias is set so the name can appear in a scan response.
            self._advertisement = ImprovAdvertisement(session)
            self._bus.export(APP_PATH, self._app)
            for path, obj in self._app.objects():
                self._bus.export(path, obj)
            self._bus.export(ADV_PATH, self._advertisement)

            packed = legacy_advertisement_length(service_data=session.service_data())
            scan_len = legacy_scan_response_length(
                manufacturer_payload=MANUFACTURER_PAYLOAD,
                local_name=SHORT_LOCAL_NAME,
            )
            logger.info(
                "Registering Improv advertisement on %s "
                "(primary PDU %s/31 bytes: flags + 128-bit Improv UUID + service data; "
                "scan response %s/31 bytes: mfg 0x%04X %s + short name %s; alias %s)",
                self._adapter_path,
                packed,
                scan_len,
                COMPANY_ID_TEST,
                MANUFACTURER_PAYLOAD.decode("ascii"),
                SHORT_LOCAL_NAME,
                local_name,
            )
            introspect = await self._bus.introspect(BLUEZ, self._adapter_path)
            proxy = self._bus.get_proxy_object(BLUEZ, self._adapter_path, introspect)
            gatt = proxy.get_interface(GATT_MANAGER)
            adv_mgr = proxy.get_interface(LE_ADVERTISING_MANAGER)
            await gatt.call_register_application(APP_PATH, {})  # type: ignore[union-attr]
            try:
                await adv_mgr.call_register_advertisement(ADV_PATH, {})  # type: ignore[union-attr]
            except Exception as exc:
                logger.warning(
                    "RegisterAdvertisement with scan-response fields failed (%s); retrying primary-only",
                    exc,
                )
                _write_ble_status(
                    {
                        "register_result": f"scan-response register failed: {exc}",
                        "last_error": str(exc),
                        "last_error_at": _now(),
                    }
                )
                # A BlueZ build without experimental ScanResponse* APIs may
                # reject the first attempt. Retry without those properties.
                self._advertisement.use_scan_response = False
                await adv_mgr.call_register_advertisement(ADV_PATH, {})  # type: ignore[union-attr]
            self._started = True
            adapter_info = await _read_adapter_info(self._bus, self._adapter_path)
            logger.info(
                "BlueZ Improv advertisement registered on %s (%s Powered=%s Discoverable=%s)",
                self._adapter_path,
                adapter_info.get("address") or "no-address",
                adapter_info.get("powered"),
                adapter_info.get("discoverable"),
            )
            _write_ble_status(
                {
                    "advertising": True,
                    "register_result": "ok",
                    "adapter": self._adapter_path,
                    "adapter_address": adapter_info.get("address") or "",
                    "powered": adapter_info.get("powered"),
                    "discoverable": adapter_info.get("discoverable"),
                    "rfkill": _rfkill_state(),
                    "primary_pdu": (
                        f"flags + 128-bit Improv UUID + service data {packed}/31"
                    ),
                    "scan_response": (
                        f"mfg 0x{COMPANY_ID_TEST:04X} {MANUFACTURER_PAYLOAD.decode('ascii')} "
                        f"+ short name {SHORT_LOCAL_NAME} {scan_len}/31"
                        if self._advertisement.use_scan_response
                        else "not included (BlueZ rejected ScanResponse* properties)"
                    ),
                    "local_name": local_name,
                    "last_error": "",
                }
            )
        except Exception as exc:
            _write_ble_status(
                {
                    "advertising": False,
                    "register_result": f"failed: {exc}",
                    "last_error": str(exc),
                    "last_error_at": _now(),
                    "rfkill": _rfkill_state(),
                    "adapter": self._adapter_path or "",
                }
            )
            await self.stop()
            raise

    async def stop(self) -> None:
        if self._bus is None:
            self._started = False
            return
        if self._adapter_path is None:
            try:
                self._bus.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._bus = None
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
    def __init__(self, session: ImprovSession) -> None:
        super().__init__("org.bluez.LEAdvertisement1")
        self._session = session
        self._service_data = session.service_data()
        # BlueZ 5.82 ScanResponse* properties are experimental; start() may
        # flip this off and retry if RegisterAdvertisement rejects them.
        self.use_scan_response = True

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

    def _manufacturer_dict(self) -> dict[int, Variant]:
        # a{qv}: company ID -> payload (no company-id prefix; BlueZ adds it)
        return {COMPANY_ID_TEST: Variant("ay", bytes(MANUFACTURER_PAYLOAD))}

    def _scan_response_data(self) -> dict[int, Variant]:
        # 0x08 = Shortened Local Name. "FS" is 4 bytes with the AD header.
        return {0x08: Variant("ay", SHORT_LOCAL_NAME.encode("ascii"))}

    @signal()
    def PropertiesChanged(self, interface: "s", changed: "a{sv}", invalidated: "as") -> None:  # type: ignore[name-defined]
        return

    @dbus_property()
    def Type(self) -> "s":  # type: ignore[name-defined]
        return "peripheral"

    @dbus_property()
    def ServiceUUIDs(self) -> "as":  # type: ignore[name-defined]
        return [SERVICE_UUID]

    @dbus_property()
    def ServiceData(self) -> "a{sv}":  # type: ignore[name-defined]
        return self._service_data_dict()

    @dbus_property()
    def ScanResponseManufacturerData(self) -> "a{qv}":  # type: ignore[name-defined]
        return self._manufacturer_dict() if self.use_scan_response else {}

    @dbus_property()
    def ScanResponseData(self) -> "a{yv}":  # type: ignore[name-defined]
        return self._scan_response_data() if self.use_scan_response else {}

    @dbus_property()
    def Discoverable(self) -> "b":  # type: ignore[name-defined]
        return True

    @dbus_property()
    def DiscoverableTimeout(self) -> "q":  # type: ignore[name-defined]
        return 0

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


async def _wait_for_adapter(bus: Any, *, wait_seconds: float = 60.0, interval: float = 2.0) -> str:
    """Wait for hci0 (or any adapter) to appear after rfkill / firmware attach."""

    import time

    deadline = time.monotonic() + wait_seconds
    last_note = 0.0
    while True:
        path = await _find_adapter(bus)
        if path:
            logger.info("BlueZ adapter ready at %s", path)
            return path
        now = time.monotonic()
        if now - last_note >= 8:
            logger.info("Waiting for a BlueZ adapter (hci0); unblocking rfkill and retrying")
            last_note = now
            _write_ble_status(
                {
                    "advertising": False,
                    "register_result": "waiting for hci0",
                    "last_error": "No BlueZ adapter yet",
                    "last_error_at": _now(),
                    "rfkill": _rfkill_state(),
                    "hci0": "missing",
                }
            )
        if now >= deadline:
            raise RuntimeError(
                "No BlueZ adapter found after waiting for hci0. "
                "Run: sudo freesounder-setup doctor"
            )
        await _rfkill_unblock()
        await _try_bring_up_controller()
        await asyncio.sleep(interval)


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
        if proc.returncode not in {0, None}:
            logger.info("rfkill unblock bluetooth exited %s", proc.returncode)
        else:
            logger.debug("rfkill unblock bluetooth succeeded")
    except OSError as exc:
        logger.info("rfkill not available: %s", exc)


async def _try_bring_up_controller() -> None:
    """Best-effort: start hciuart if present, load UART Bluetooth modules."""

    for unit in ("hciuart.service", "bluetooth.service"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl",
                "start",
                unit,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except OSError:
            pass
    for mod in ("btbcm", "hci_uart"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "modprobe",
                mod,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except OSError:
            pass


def _now() -> str:
    from freesounder_ble_provisioning.status import now_iso

    return now_iso()


def _write_ble_status(update: dict) -> None:
    try:
        from freesounder_ble_provisioning.status import write_status

        write_status(update)
    except Exception:  # noqa: BLE001
        logger.debug("could not write ble status", exc_info=True)


def _rfkill_state() -> str:
    try:
        import subprocess

        proc = subprocess.run(
            ["rfkill", "-n", "-o", "TYPE,SOFT,HARD", "list", "bluetooth"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    text = (proc.stdout or "").strip()
    if not text:
        return "unknown"
    soft_blocked = False
    hard_blocked = False
    for line in text.splitlines():
        parts = [p.strip().lower() for p in line.replace("	", " ").split()]
        if not parts:
            continue
        # TYPE SOFT HARD  — values are unblocked/blocked
        if "bluetooth" in parts[0] or True:
            if any(p == "blocked" for p in parts[1:2]):
                soft_blocked = True
            if any(p == "blocked" for p in parts[2:3]):
                hard_blocked = True
    if hard_blocked and soft_blocked:
        return "soft+hard-blocked"
    if hard_blocked:
        return "hard-blocked"
    if soft_blocked:
        return "soft-blocked"
    return "unblocked"


async def _read_adapter_info(bus: Any, adapter_path: str) -> dict[str, Any]:
    info: dict[str, Any] = {"address": "", "powered": None, "discoverable": None}
    try:
        introspect = await bus.introspect(BLUEZ, adapter_path)
        proxy = bus.get_proxy_object(BLUEZ, adapter_path, introspect)
        props = proxy.get_interface(DBUS_PROPS)
        raw = await props.call_get_all(ADAPTER_IFACE)
        def _val(name: str):
            item = raw.get(name)
            return item.value if item is not None and hasattr(item, "value") else item
        info["address"] = str(_val("Address") or "")
        info["powered"] = bool(_val("Powered"))
        info["discoverable"] = bool(_val("Discoverable"))
    except Exception:  # noqa: BLE001
        logger.debug("could not read adapter properties", exc_info=True)
    return info
