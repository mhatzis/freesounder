"""The bootfs doctor must not be rebuilt on every 2 s poll (0.6.17)."""

from freesounder_ble_provisioning.service import DoctorThrottle


def test_first_poll_writes() -> None:
    t = DoctorThrottle(offline_interval=60)
    assert t.due((True, "eth0", False), online=True, now=0)


def test_online_and_unchanged_never_rewrites() -> None:
    t = DoctorThrottle(offline_interval=60)
    state = (True, "wlan0/192.168.1.5 (usable)", False)
    t.mark(state, now=0)
    for now in (2, 60, 600, 86_400):
        assert not t.due(state, online=True, now=now)


def test_state_change_rewrites_immediately() -> None:
    t = DoctorThrottle(offline_interval=60)
    t.mark((True, "a", False), now=0)
    assert t.due((False, "no IPv4 addresses", True), online=False, now=2)
    assert t.due((True, "b", False), online=True, now=2)


def test_offline_rewrites_at_most_once_per_interval() -> None:
    t = DoctorThrottle(offline_interval=60)
    state = (False, "no IPv4 addresses", True)
    t.mark(state, now=0)
    assert not t.due(state, online=False, now=2)
    assert not t.due(state, online=False, now=59)
    assert t.due(state, online=False, now=60)
