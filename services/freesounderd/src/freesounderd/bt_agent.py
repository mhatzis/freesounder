"""BlueZ pairing agent used only while the app pairs a Bluetooth speaker.

No ``from __future__ import annotations`` here: dbus-next reads the D-Bus
signatures from the annotation strings.

It answers just one device (the speaker being paired) and rejects every
other request, and it is unregistered again right after pairing.
"""

try:
    from dbus_next import DBusError
    from dbus_next.service import ServiceInterface, method
except ImportError:  # pragma: no cover - only on a player
    ServiceInterface = object  # type: ignore[assignment,misc]

    def method(*_a, **_k):  # type: ignore[no-redef]
        return lambda fn: fn

    class DBusError(Exception):  # type: ignore[no-redef]
        def __init__(self, name: str, text: str) -> None:
            super().__init__(text)


AGENT_PATH = "/org/freesounder/btagent"
REJECTED = "org.bluez.Error.Rejected"


class PairingAgent(ServiceInterface):  # type: ignore[misc]
    def __init__(self, device_path: str) -> None:
        super().__init__("org.bluez.Agent1")
        self.device_path = device_path

    def _check(self, device: str) -> None:
        if device != self.device_path:
            raise DBusError(REJECTED, "not the speaker being paired")

    @method()
    def Release(self):  # noqa: N802
        return None

    @method()
    def RequestPinCode(self, device: "o") -> "s":  # noqa: F821, N802
        self._check(device)
        return "0000"  # legacy (pre-2.1) speakers almost all use 0000

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821, N802
        self._check(device)

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # noqa: F821, N802
        self._check(device)
        return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: F821, N802
        self._check(device)

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821, N802
        self._check(device)

    @method()
    def RequestAuthorization(self, device: "o"):  # noqa: F821, N802
        self._check(device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821, N802
        self._check(device)

    @method()
    def Cancel(self):  # noqa: N802
        return None
