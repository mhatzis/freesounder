from freesounder_ble_provisioning.doctor import collect_report


def test_doctor_report_has_expected_sections() -> None:
    report = collect_report()
    assert "FreeSounder BLE provisioning doctor" in report
    assert "== Adapter ==" in report
    assert "== rfkill ==" in report
    assert "== Network ==" in report
    assert "31" in report
