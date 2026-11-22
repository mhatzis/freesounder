import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/models/player_settings.dart';

void main() {
  test('a fixed developer password is read and the password is not sent', () {
    final status = SshDeveloperStatus.fromJson({
      'enabled': true,
      'hostname': 'fs-office',
      'command': 'ssh developer@fs-office.local',
      'password': null,
      'password_fixed': true,
    });
    expect(status.enabled, isTrue);
    expect(status.password, isNull);
    expect(status.passwordFixed, isTrue);
  });

  test('older players without password_fixed default to a one-time password', () {
    final status = SshDeveloperStatus.fromJson({
      'enabled': true,
      'hostname': 'fs-office',
      'password': 'abc123',
    });
    expect(status.passwordFixed, isFalse);
    expect(status.password, 'abc123');
  });
}
