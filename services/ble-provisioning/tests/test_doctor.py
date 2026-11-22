from freesounder_ble_provisioning.doctor import collect_report


def test_doctor_report_has_expected_sections() -> None:
    report = collect_report()
    assert "FreeSounder BLE provisioning doctor" in report
    assert "== Adapter ==" in report
    assert "== rfkill ==" in report
    assert "== Network ==" in report
    assert "31" in report


def test_doctor_never_runs_bluetoothctl(monkeypatch) -> None:
    """bluetoothctl registers an advertisement monitor with bluetoothd on every
    run; the doctor must stay read-only (0.6.17 Bluetooth dropouts)."""
    import shutil
    import subprocess

    from freesounder_ble_provisioning import doctor

    ran: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):
        if isinstance(cmd, list | tuple):
            ran.append([str(c) for c in cmd])
        return real_run(["true"], *args, **kwargs)

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", spy)
    doctor.collect_report()
    assert ran, "expected the doctor to run some tools"
    assert not any(cmd and cmd[0].endswith("bluetoothctl") for cmd in ran)
