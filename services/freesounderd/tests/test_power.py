import json
import struct
from pathlib import Path

import pytest

from freesounderd import cpu, diagnostics, power
from freesounderd.cpu import ProcSampler, ThermalGuard


def test_pmic_reset_bits_follow_raspberry_pi_docs() -> None:
    assert power.decode_pmic_reset(0x02) == ["under-voltage"]
    assert power.decode_pmic_reset(0x14) == ["over-temperature", "watchdog"]
    assert power.decode_pmic_reset(0) == []
    assert power.decode_pmic_reset(None) == []


def test_pi5_device_tree_is_read_big_endian(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dt = tmp_path / "power"
    dt.mkdir()
    (dt / "power_reset").write_bytes(struct.pack(">I", 2))
    (dt / "max_current").write_bytes(struct.pack(">I", 3000))
    (dt / "usb_over_current_detected").write_bytes(struct.pack(">I", 0))
    boot = tmp_path / "bootloader"
    boot.mkdir()
    (boot / "rsts").write_bytes(struct.pack(">I", 0x1000))
    monkeypatch.setattr(power, "DT_POWER", dt)
    monkeypatch.setattr(power, "DT_BOOTLOADER", boot)
    info = power.pi5_power_info()
    assert info["pmic_reset"] == "0x2"
    assert info["pmic_reset_reason"] == ["under-voltage"]
    assert info["psu_max_current_ma"] == 3000
    assert info["usb_over_current_at_boot"] is False
    assert info["bootloader_rsts"] == "0x1000"


def test_not_a_pi5_gives_empty_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(power, "DT_POWER", tmp_path / "nope")
    monkeypatch.setattr(power, "DT_BOOTLOADER", tmp_path / "nope")
    info = power.pi5_power_info()
    assert info["pmic_reset"] is None and info["psu_max_current_ma"] is None


def _watch(tmp_path: Path, boot: str) -> power.Watch:
    w = power.Watch(tmp_path)
    w.boot = boot
    w.read_flags = lambda: 0
    w.read_ext5v = lambda: 5.1
    w.read_temp = lambda: 50.0
    w.guard = ThermalGuard(policy=tmp_path / "no-cpufreq", mode_file=tmp_path / "no-mode")
    return w


def test_heartbeat_survives_a_crash_and_is_archived_next_boot(tmp_path: Path) -> None:
    first = _watch(tmp_path, "boot-a")
    volts = iter([5.1, 4.62, 5.05])
    first.read_ext5v = lambda: next(volts)
    flags = iter([0, 0x50005, 0x50000])
    first.read_flags = lambda: next(flags)
    for _ in range(3):
        first.beat()
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert hb["boot_id"] == "boot-a" and hb["clean"] is False and hb["min_ext5v"] == 4.62
    history = json.loads((tmp_path / "power-history.json").read_text())
    assert [h["throttled"] for h in history] == ["0x0", "0x50005", "0x50000"]

    # Power lost here; the next boot archives it.
    second = _watch(tmp_path, "boot-b")
    prev = second.archive_previous()
    assert prev is not None and prev["boot_id"] == "boot-a"
    last = diagnostics.describe_last_boot(json.loads((tmp_path / "last-boot.json").read_text()))
    assert last is not None
    assert last["clean_shutdown"] is False and last["ext5v_min"] == 4.62
    assert "under-voltage since boot" in " ".join(last["throttled_flags"])
    # Same boot restarting the service does not overwrite the archive.
    second.beat()
    assert _watch(tmp_path, "boot-b").archive_previous() is None


def test_clean_shutdown_is_marked(tmp_path: Path) -> None:
    w = _watch(tmp_path, "boot-a")
    w.beat(clean=True, shutdown="poweroff")
    nxt = _watch(tmp_path, "boot-b")
    prev = nxt.archive_previous()
    assert prev is not None and prev["clean"] and prev["shutdown"] == "poweroff"


def test_previous_boot_is_found_across_machine_id_change() -> None:
    boots = json.dumps(
        [
            {"index": -1, "boot_id": "aaa", "first_entry": 100, "last_entry": 200},
            {"index": 0, "boot_id": "ccc", "first_entry": 500, "last_entry": 600},
            # merged list from a second machine-id directory
            {"index": 0, "boot_id": "bbb", "first_entry": 300, "last_entry": 400},
        ]
    )
    assert diagnostics.previous_boot(boots, "ccc") == "bbb"
    assert diagnostics.previous_boot(boots, "aaa") is None
    # Current boot not flushed yet: the newest listed boot is the previous one.
    assert diagnostics.previous_boot(boots, "ddd") == "ccc"


def test_first_boot_has_no_previous_boot() -> None:
    one = json.dumps([{"index": 0, "boot_id": "ccc", "first_entry": 5}])
    assert diagnostics.previous_boot(one, "ccc") is None
    assert diagnostics.previous_boot("", "ccc") is None
    assert diagnostics.previous_boot("not json", "ccc") is None


def test_dashed_boot_ids_match() -> None:
    boots = json.dumps([{"boot_id": "11", "first_entry": 1}, {"boot_id": "22", "first_entry": 2}])
    assert diagnostics.previous_boot(boots, "2-2") == "11"


def test_heartbeat_ending_beats_empty_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _watch(tmp_path, "old")
    w.beat()
    _watch(tmp_path, "new").archive_previous()

    async def fake_run(*cmd: str, wait: float = 8.0) -> str:
        return "[]" if "--list-boots" in cmd else ""

    monkeypatch.setattr(diagnostics, "_run", fake_run)
    monkeypatch.setattr(diagnostics, "ext5v", lambda: None)
    import asyncio

    data = asyncio.run(diagnostics.collect(tmp_path))
    assert data["previous_boot_ended"].startswith("stopped without a clean shutdown around")
    assert data["previous_boot"]["clean_shutdown"] is False


def _policy(tmp_path: Path, cur: int = 2_400_000) -> Path:
    pol = tmp_path / "policy0"
    pol.mkdir()
    for name, value in (
        ("cpuinfo_min_freq", 1_500_000),
        ("cpuinfo_max_freq", 2_400_000),
        ("scaling_max_freq", cur),
        ("scaling_cur_freq", 1_500_000),
    ):
        (pol / name).write_text(f"{value}\n")
    (pol / "scaling_governor").write_text("ondemand\n")
    return pol


def test_thermal_guard_caps_when_hot_and_restores_when_cool(tmp_path: Path) -> None:
    pol = _policy(tmp_path)
    guard = ThermalGuard(policy=pol, mode_file=tmp_path / "mode")
    assert guard.update(60.0) is None
    line = guard.update(76.0)
    assert line and "capped at 1500 MHz" in line
    assert (pol / "scaling_max_freq").read_text().strip() == "1500000"
    assert guard.update(70.0) is None  # hysteresis: stays capped
    line = guard.update(64.0)
    assert line and "back to 2400 MHz" in line
    assert (pol / "scaling_max_freq").read_text().strip() == "2400000"


def test_thermal_guard_never_caps_below_the_hardware_minimum(tmp_path: Path) -> None:
    pol = _policy(tmp_path)
    (pol / "cpuinfo_min_freq").write_text("1800000\n")
    guard = ThermalGuard(policy=pol, mode_file=tmp_path / "mode")
    guard.update(90.0)
    assert int((pol / "scaling_max_freq").read_text()) >= 1_800_000
    assert cpu.CAP_KHZ >= 1_500_000


def test_thermal_guard_modes_and_restart(tmp_path: Path) -> None:
    pol = _policy(tmp_path, cur=1_500_000)
    mode = tmp_path / "mode"
    mode.write_text("off\n")
    guard = ThermalGuard(policy=pol, mode_file=mode)
    assert guard.capped  # left capped by a previous run: restore it
    assert "back to" in (guard.update(90.0) or "")
    mode.write_text("cool\n")
    assert "capped" in (guard.update(40.0) or "")
    guard.release()
    assert (pol / "scaling_max_freq").read_text().strip() == "2400000"


def _fake_proc(root: Path, procs: dict[int, tuple[str, int]], total: int, idle: int) -> None:
    root.mkdir(exist_ok=True)
    (root / "stat").write_text(f"cpu  {total - idle} 0 0 {idle} 0 0 0 0 0 0\n")
    (root / "loadavg").write_text("0.52 0.40 0.31 1/123 4567\n")
    for pid, (name, ticks) in procs.items():
        d = root / str(pid)
        d.mkdir(exist_ok=True)
        rest = " ".join(["S"] + ["0"] * 10 + [str(ticks), "0"] + ["0"] * 30)
        (d / "stat").write_text(f"{pid} ({name}) {rest}\n")


def test_top_processes_are_percent_of_one_core(tmp_path: Path) -> None:
    root = tmp_path / "proc"
    _fake_proc(
        root, {10: ("snapserver", 100), 11: ("ffmpeg", 50), 12: ("my (odd) name", 0)}, 4000, 3800
    )
    s = ProcSampler(proc=root, ncpu=4)
    assert s.sample() is None
    # 400 ticks over 4 cores = 100 ticks of wall time per core.
    _fake_proc(
        root, {10: ("snapserver", 150), 11: ("ffmpeg", 60), 12: ("my (odd) name", 5)}, 4400, 4100
    )
    out = s.sample()
    assert out is not None
    assert [p["name"] for p in out["top"]] == ["snapserver", "ffmpeg", "my (odd) name"]
    assert out["top"][0]["cpu"] == 50.0  # 50 ticks of 100 per core
    assert out["cpu_busy"] == 25.0  # busy 200 -> 300: 100 of 400 ticks
    assert cpu.loadavg(root) == [0.52, 0.40, 0.31]
    assert cpu.freq_info(_policy(tmp_path)) == {
        "cur_mhz": 1500,
        "max_mhz": 2400,
        "hw_min_mhz": 1500,
        "hw_max_mhz": 2400,
        "governor": "ondemand",
    }


def test_heartbeat_records_cpu_and_logs_top_when_hot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    w = _watch(tmp_path, "boot-x")
    root = tmp_path / "proc"
    _fake_proc(root, {10: ("snapclient", 10)}, 1000, 900)
    w.sampler = ProcSampler(proc=root, ncpu=1)
    w.read_freq = lambda: {"cur_mhz": 2400, "max_mhz": 2400, "governor": "ondemand"}
    w.read_load = lambda: [1.0, 0.5, 0.2]
    w.beat()
    _fake_proc(root, {10: ("snapclient", 40)}, 1100, 950)
    w.read_temp = lambda: 81.0
    rec = w.beat()
    assert rec["load"] == [1.0, 0.5, 0.2] and rec["cpu_mhz"] == 2400
    assert rec["top"][0] == {"pid": 10, "name": "snapclient", "cpu": 30.0}
    out = capsys.readouterr().out
    assert "HOT: CPU 81.0 C" in out and "snapclient 30.0%" in out
    saved = json.loads((tmp_path / "heartbeat.json").read_text())
    assert saved["top"][0]["name"] == "snapclient"
