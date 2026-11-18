import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/ui/room_settings_sheet.dart';

void main() {
  test('shows heartbeat, PMIC reason, PSU and power history', () {
    final text = formatDiagnostics({
      'previous_boot_ended': 'stopped without a clean shutdown around 2026-11-18 20:01:02 +1100 (up 42.5 min)',
      'previous_boot': {
        'clean_shutdown': false,
        'last_heartbeat': '2026-11-18 20:01:02 +1100',
        'uptime_at_end_min': 42.5,
        'ext5v_last': 4.9,
        'ext5v_min': 4.61,
        'throttled': '0x50005',
        'throttled_flags': ['under-voltage now'],
      },
      'pmic_reset': '0x2',
      'pmic_reset_reason': ['under-voltage'],
      'psu_max_current_ma': 3000,
      'throttled': '0x0',
      'throttled_flags': <String>[],
      'input_voltage': '5.08 V',
      'input_voltage_min_this_boot': 5.02,
      'power_history': [
        {'time': 1795000000, 'uptime': 120, 'throttled': '0x50005', 'ext5v': 4.61, 'temp': 61.0},
      ],
    });
    expect(text, contains('Last heartbeat: 2026-11-18 20:01:02 +1100 (up 42.5 min)'));
    expect(text, contains('Clean shutdown: no'));
    expect(text, contains('lowest 4.61 V'));
    expect(text, contains('Pi 5 PMIC reset reason: 0x2 (under-voltage)'));
    expect(text, contains('3000 mA (not a 5 A supply)'));
    expect(text, contains('lowest this boot 5.02 V'));
    expect(text, contains('Power flag history:'));
    expect(text, contains('0x50005  4.61 V'));
  });

  test('older players (string reset reason) still format', () {
    final text = formatDiagnostics({'previous_boot_ended': 'unknown', 'pmic_reset_reason': '00000002'});
    expect(text, startsWith('Previous boot ended: unknown'));
  });
}
