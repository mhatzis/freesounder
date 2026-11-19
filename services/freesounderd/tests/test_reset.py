"""Factory-reset triggers: tap counting and the power-cycle boot counter."""

from __future__ import annotations

from freesounderd.reset import BootCounter, TapCounter, decode_throttled, power_key_devices


def taps(counter: TapCounter, times: list[float]) -> list[str | None]:
    return [counter.press(t) for t in times]


def test_single_press_is_shutdown_after_quiet() -> None:
    c = TapCounter()
    assert c.press(10.0) is None
    assert c.poll(10.5) is None  # still waiting for more presses
    assert c.poll(11.3) == "shutdown"
    assert c.poll(20) is None


def test_five_quick_presses_reset_at_once() -> None:
    c = TapCounter()
    assert taps(c, [0.0, 0.5, 1.0, 1.6, 2.2]) == [None, None, None, None, "reset"]
    assert c.poll(10) is None, "no shutdown after a reset"


def test_two_to_four_presses_do_nothing() -> None:
    for n in (2, 3, 4):
        c = TapCounter()
        taps(c, [0.4 * i for i in range(n)])
        assert c.poll(5) == "ignored"


def test_five_slow_presses_never_reset_or_shut_down() -> None:
    c = TapCounter()
    # 0.9 s apart: inside the settle gap, but 5 presses take 3.6 s > window.
    results = taps(c, [0.9 * i for i in range(5)])
    assert "reset" not in results
    assert c.poll(10) == "ignored"


def test_presses_after_a_long_gap_start_over() -> None:
    c = TapCounter()
    taps(c, [0, 0.3, 0.6, 0.9])
    assert c.poll(1.0) is None
    # (a real daemon polls in between; simulate a sequence that stalled)
    c.presses = [0, 0.3, 0.6, 0.9]
    assert c.press(9.0) is None  # gap > window: this is press 1 of a new run
    assert c.presses == [9.0]


def test_boot_counter_resets_after_five_cut_boots(tmp_path) -> None:
    b = BootCounter(tmp_path / "short-boots")
    for i in range(1, 6):
        assert b.on_boot() == f"armed {i}/5"  # each boot cut before clear()
    assert b.on_boot() == "reset"  # 6th boot: the one left on
    assert b.count() == 0


def test_normal_boots_and_single_power_cuts_never_add_up(tmp_path) -> None:
    b = BootCounter(tmp_path / "short-boots")
    for _ in range(20):
        b.on_boot()
        b.on_boot()  # one power cut while armed
        b.on_boot()
        b.clear()  # ... then a boot that ran past the window
        assert b.count() == 0
    assert not (tmp_path / "short-boots").exists()


def test_four_cuts_then_a_normal_boot_start_over(tmp_path) -> None:
    b = BootCounter(tmp_path / "short-boots")
    for _ in range(4):
        b.on_boot()
    b.clear()
    for i in range(1, 6):
        assert b.on_boot() == f"armed {i}/5"


def test_undervoltage_boots_are_not_counted(tmp_path) -> None:
    b = BootCounter(tmp_path / "short-boots")
    for _ in range(4):
        b.on_boot()
    assert b.on_boot(power_ok=False) == "undervoltage"
    assert b.count() == 0


def test_corrupt_counter_file_counts_as_zero(tmp_path) -> None:
    path = tmp_path / "short-boots"
    path.write_bytes(b"\x00\xff")
    assert BootCounter(path).on_boot() == "armed 1/5"


PROC = """I: Bus=0019 Vendor=0001 Product=0001 Version=0100
N: Name="pwr_button"
H: Handlers=kbd event0
B: EV=3
B: KEY=10000000000000 0

I: Bus=0003 Vendor=046d Product=c31c Version=0110
N: Name="USB Keyboard"
H: Handlers=sysrq kbd leds event1
B: KEY=1000000000007 ff9f207ac14057ff febeffdfffefffff fffffffffffffffe

I: Bus=0019 Vendor=0001 Product=0001 Version=0100
N: Name="vc4-hdmi"
H: Handlers=event2
B: KEY=0
"""


def test_finds_power_key_devices() -> None:
    # KEY_POWER (116) = bit 52 of the low word -> 0x10000000000000.
    assert power_key_devices(PROC)[0] == "/dev/input/event0"
    assert "/dev/input/event2" not in power_key_devices(PROC)


def test_decode_throttled() -> None:
    assert decode_throttled(0x50005) == [
        "under-voltage now",
        "throttled now",
        "under-voltage since boot",
        "throttled since boot",
    ]
    assert decode_throttled(0) == []


def test_wired_button_pin_choice() -> None:
    from freesounderd.reset import button_line

    assert button_line("", "auto", i2c_in_use=False) == "GPIO3"
    assert button_line("gpio=auto\n", "hifiberry-dac", i2c_in_use=False) == "GPIO3"
    # I2C DAC/amp (codec on GPIO2/3) or any I2C HAT: GPIO3 stays free.
    assert button_line("", "hifiberry-amp2", i2c_in_use=False) is None
    assert button_line("", "auto", i2c_in_use=True) is None
    assert button_line("gpio=3", "iqaudio-dacplus", i2c_in_use=False) is None
    # Another pin works with an I2C amp (no wake from halt there).
    assert button_line("gpio=17", "hifiberry-amp4pro", i2c_in_use=True) == "GPIO17"
    assert button_line("gpio=off", "none", i2c_in_use=False) is None
    assert button_line("gpio=banana", "none", i2c_in_use=False) is None


def test_i2c_in_use_sees_dac_overlay_without_i2c_dev(tmp_path):
    from freesounderd.reset import i2c_in_use

    assert i2c_in_use(tmp_path) is False
    boot = tmp_path / "boot/firmware"
    boot.mkdir(parents=True)
    (boot / "config.txt").write_text(
        "[all]\n# --- freesounder-dac-start ---\n[all]\ndtparam=audio=on\n"
        "dtoverlay=hifiberry-dacplus-std\n# --- freesounder-dac-end ---\n"
    )
    assert i2c_in_use(tmp_path) is True
    (boot / "config.txt").write_text(
        "# --- freesounder-dac-start ---\n[all]\ndtparam=audio=on\n# --- freesounder-dac-end ---\n"
    )
    assert i2c_in_use(tmp_path) is False
    adapter = tmp_path / "sys/bus/i2c/devices/i2c-1"
    adapter.mkdir(parents=True)
    assert i2c_in_use(tmp_path) is True
