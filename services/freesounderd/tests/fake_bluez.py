# ruff: noqa: F722, F821, N802
"""A tiny fake BlueZ + bluealsa on a private D-Bus, for BluezBackend tests.

No ``from __future__ import annotations``: dbus-next reads signatures from
the annotation strings.
"""

from dbus_next import DBusError, Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.message import Message
from dbus_next.service import ServiceInterface, dbus_property, method

SPEAKER = "AA:BB:CC:DD:EE:01"
DEV_PATH = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01"


class Adapter(ServiceInterface):
    def __init__(self, world):
        super().__init__("org.bluez.Adapter1")
        self.world = world
        self._pairable = False

    @dbus_property(access=PropertyAccess.READ)
    def Address(self) -> "s":
        return "B8:27:EB:00:00:01"

    @dbus_property(access=PropertyAccess.READ)
    def Powered(self) -> "b":
        return True

    @dbus_property()
    def Pairable(self) -> "b":
        return self._pairable

    @Pairable.setter
    def Pairable(self, value: "b"):
        self._pairable = value
        self.world.log.append(("Pairable", value))

    @method()
    def SetDiscoveryFilter(self, flt: "a{sv}"):
        self.world.log.append(("filter", {k: v.value for k, v in flt.items()}))

    @method()
    def StartDiscovery(self):
        self.world.log.append(("start",))
        self.world.add_speaker()

    @method()
    def StopDiscovery(self):
        self.world.log.append(("stop",))

    @method()
    def RemoveDevice(self, path: "o"):
        self.world.log.append(("remove", path))
        self.world.bus.unexport(path)
        self.world.device = None


class Device(ServiceInterface):
    def __init__(self, world):
        super().__init__("org.bluez.Device1")
        self.world = world
        self.paired = False
        self.trusted = False
        self.connected = False

    @dbus_property(access=PropertyAccess.READ)
    def Address(self) -> "s":
        return SPEAKER

    @dbus_property(access=PropertyAccess.READ)
    def Alias(self) -> "s":
        return "Patio Boom"

    @dbus_property(access=PropertyAccess.READ)
    def Class(self) -> "u":
        return 0x240414

    @dbus_property(access=PropertyAccess.READ)
    def UUIDs(self) -> "as":
        return ["0000110b-0000-1000-8000-00805f9b34fb"]

    @dbus_property(access=PropertyAccess.READ)
    def Paired(self) -> "b":
        return self.paired

    @dbus_property(access=PropertyAccess.READ)
    def Connected(self) -> "b":
        return self.connected

    @dbus_property()
    def Trusted(self) -> "b":
        return self.trusted

    @Trusted.setter
    def Trusted(self, value: "b"):
        self.trusted = value

    @method()
    async def Pair(self):
        if self.world.agent is None:
            raise DBusError("org.bluez.Error.AuthenticationFailed", "no agent")
        sender, path = self.world.agent
        reply = await self.world.bus.call(
            Message(
                destination=sender,
                path=path,
                interface="org.bluez.Agent1",
                member="RequestConfirmation",
                signature="ou",
                body=[DEV_PATH, 123456],
            )
        )
        self.world.log.append(("confirm", reply.message_type.name))
        if reply.message_type.name == "ERROR":
            raise DBusError("org.bluez.Error.AuthenticationRejected", "rejected")
        # A request for another device must be rejected by the agent.
        other = await self.world.bus.call(
            Message(
                destination=sender,
                path=path,
                interface="org.bluez.Agent1",
                member="RequestAuthorization",
                signature="o",
                body=["/org/bluez/hci0/dev_11_22_33_44_55_66"],
            )
        )
        self.world.log.append(("other", other.message_type.name))
        self.paired = True

    @method()
    def ConnectProfile(self, uuid: "s"):
        self.world.log.append(("connect", uuid))
        self.connected = True
        self.world.add_pcm()

    @method()
    def Connect(self):
        self.connected = True

    @method()
    def Disconnect(self):
        self.connected = False


class AgentManager(ServiceInterface):
    def __init__(self, world):
        super().__init__("org.bluez.AgentManager1")
        self.world = world

    @method()
    def RegisterAgent(self, path: "o", capability: "s"):
        self.world.log.append(("agent", capability))
        self.world.agent = (self.world.client_name, path)

    @method()
    def UnregisterAgent(self, path: "o"):
        self.world.log.append(("agent-off",))
        self.world.agent = None


class Pcm(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluealsa.PCM1")

    @dbus_property(access=PropertyAccess.READ)
    def Device(self) -> "o":
        return DEV_PATH

    @dbus_property(access=PropertyAccess.READ)
    def Codec(self) -> "s":
        return "SBC"


class World:
    def __init__(self, bus, alsa_bus):
        self.bus = bus
        self.alsa_bus = alsa_bus
        self.log = []
        self.agent = None
        self.client_name = None
        self.device = None
        bus.export("/org/bluez", AgentManager(self))
        bus.export("/org/bluez/hci0", Adapter(self))

    def add_speaker(self):
        if self.device is None:
            self.device = Device(self)
            self.bus.export(DEV_PATH, self.device)

    def add_pcm(self):
        self.alsa_bus.export("/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_01/a2dpsrc/sink", Pcm())


async def start(address):
    bus = await MessageBus(bus_address=address).connect()
    await bus.request_name("org.bluez")
    alsa = await MessageBus(bus_address=address).connect()
    await alsa.request_name("org.bluealsa")
    return World(bus, alsa)


_ = Variant  # re-exported for tests
