import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/improv.dart';

void main() {
  test('RPC round-trip and wifi payload', () {
    final payload = encodeWifiSettings(ssid: 'Home', password: 'secret');
    final frame = encodeImprovRpc(improvCmdWifiSettings, payload);
    final parsed = tryParseImprovRpc(frame)!;
    expect(parsed.command, improvCmdWifiSettings);
    expect(parsed.data, payload);
    expect(tryParseImprovRpc(frame.sublist(0, 3)), isNull);

    final result = encodeImprovRpc(improvCmdDeviceInfo, encodeImprovStrings(['FreeSounder', '0.3.0', 'pi', 'Kitchen', 'abc']));
    expect(decodeImprovResult(result), ['FreeSounder', '0.3.0', 'pi', 'Kitchen', 'abc']);
  });

  test('bad checksum throws', () {
    final frame = encodeImprovRpc(improvCmdIdentify, const []);
    final bad = [...frame];
    bad[bad.length - 1] = bad.last ^ 0xFF;
    expect(() => tryParseImprovRpc(bad), throwsFormatException);
  });
}
