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

  test('shows CPU load, clock, governor and top processes (now and at the end of the last boot)', () {
    final text = formatDiagnostics({
      'previous_boot_ended': 'stopped without a clean shutdown',
      'previous_boot': {
        'clean_shutdown': false,
        'temp': 81.2,
        'load': [1.5, 1.2, 0.9],
        'cpu_mhz': 1500,
        'clock_capped': true,
        'top': [
          {'pid': 1, 'name': 'snapserver', 'cpu': 4.0},
          {'pid': 2, 'name': 'ffmpeg', 'cpu': 2.5},
        ],
      },
      'temperature': "62.0'C",
      'cpu_load': [0.31, 0.25, 0.2],
      'cpu_busy': '3.1%',
      'cpu_mhz': 1500,
      'cpu_max_mhz': 2400,
      'cpu_hw_max_mhz': 2400,
      'cpu_governor': 'ondemand',
      'cpu_clock_capped': false,
      'cpu_mode': 'auto',
      'top_processes': [
        {'pid': 3, 'name': 'snapclient', 'cpu': 1.7},
      ],
      'power_history': [
        {'time': 1795000000, 'uptime': 2100, 'throttled': '0x80008', 'ext5v': 4.9, 'temp': 80.5, 'cpu_mhz': 1500,
          'top': [{'pid': 1, 'name': 'snapserver', 'cpu': 4.0}]},
      ],
    });
    expect(text, contains('CPU then: 81.2°C, 1500 MHz (capped), load 1.50 / 1.20 / 0.90'));
    expect(text, contains('Top processes then: snapserver 4.0%, ffmpeg 2.5%'));
    expect(text, contains('CPU load (1/5/15 min): 0.31 / 0.25 / 0.20, busy 3.1%'));
    expect(text, contains('CPU clock: 1500 MHz (max 2400 of 2400 MHz, ondemand)'));
    expect(text, contains('Top processes: snapclient 1.7%'));
    expect(text, contains('80.5°C  1500 MHz  top: snapserver 4.0%'));
  });
}
