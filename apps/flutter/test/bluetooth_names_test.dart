import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/models/bluetooth_output.dart';

void main() {
  test('a real name is shown as is', () {
    final s = BtSpeaker.fromJson({'mac': '18:66:96:C3:7A:28', 'name': 'IKEA VAPPEBY', 'kind': 'speaker', 'signal': 'good'});
    expect(s.name, 'IKEA VAPPEBY');
    expect(s.hasName, isTrue);
    expect(s.signal, 'good');
  });

  test('no name yet gives a friendly stand-in, not a MAC', () {
    final s = BtSpeaker.fromJson({'mac': '18:66:96:C3:7A:28', 'name': null});
    expect(s.name, 'Bluetooth speaker (7A:28)');
    expect(s.hasName, isFalse);
  });

  test("an older player's dashed address is not treated as a name", () {
    final s = BtSpeaker.fromJson({'mac': '18:66:96:C3:7A:28', 'name': '18-66-96-C3-7A-28'});
    expect(s.hasName, isFalse);
    expect(s.name, 'Bluetooth speaker (7A:28)');
  });
}
