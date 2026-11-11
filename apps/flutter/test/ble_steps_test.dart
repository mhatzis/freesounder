import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/ble_steps.dart';
import 'package:freesounder/api/improv.dart';
import 'package:freesounder/api/nearby.dart';

void main() {
  group('awaitRadioOn', () {
    test('waits through unknown (CBManagerStateUnknown) until on', () async {
      final states = StreamController<RadioState>();
      final done = awaitRadioOn(states.stream, timeout: const Duration(seconds: 1));
      states.add(RadioState.unknown);
      await Future<void>.delayed(const Duration(milliseconds: 20));
      states.add(RadioState.turningOn);
      states.add(RadioState.on);
      await done;
      await states.close();
    });

    test('unauthorized gives a Settings hint, not a raw exception', () async {
      final err = await awaitRadioOn(
        Stream.fromIterable([RadioState.unknown, RadioState.unauthorized]),
      ).then<Object?>((_) => null, onError: (Object e) => e);
      expect(err, isA<NearbyException>());
      final nearby = err! as NearbyException;
      expect(nearby.settingsHint, isTrue);
      expect(nearby.message, contains('Settings'));
      expect(nearby.message, isNot(contains('Exception')));
    });

    test('off times out with turn-on hint', () async {
      final states = StreamController<RadioState>();
      final future = awaitRadioOn(states.stream, timeout: const Duration(milliseconds: 50));
      states.add(RadioState.off);
      await expectLater(
        future,
        throwsA(isA<NearbyException>().having((e) => e.message, 'message', contains('Bluetooth is off'))),
      );
      await states.close();
    });

    test('stuck in unknown says not ready', () async {
      final states = StreamController<RadioState>();
      final future = awaitRadioOn(states.stream, timeout: const Duration(milliseconds: 50));
      states.add(RadioState.unknown);
      await expectLater(
        future,
        throwsA(isA<NearbyException>().having((e) => e.message, 'message', contains('not ready'))),
      );
      await states.close();
    });
  });

  group('runStep', () {
    test('a hang becomes a timeout error naming the step', () async {
      final steps = <String>[];
      final never = Completer<void>();
      await expectLater(
        runStep(
          BleStep.deviceInfo,
          () => never.future,
          timeout: const Duration(milliseconds: 30),
          onStep: (step, error) => steps.add('${step.name}:${error ?? 'start'}'),
        ),
        throwsA(
          isA<NearbyException>()
              .having((e) => e.step, 'step', BleStep.deviceInfo)
              .having((e) => e.message, 'message', contains('did not answer in time'))
              .having((e) => e.debugLine, 'debug', contains('timed out')),
        ),
      );
      expect(steps.first, 'deviceInfo:start');
      expect(steps.last, startsWith('deviceInfo:timed out'));
    });

    test('platform errors are hidden behind a friendly message', () async {
      await expectLater(
        runStep(
          BleStep.connect,
          () => Future<void>.error(Exception('PlatformException(connect, raw)')),
          timeout: const Duration(seconds: 1),
        ),
        throwsA(
          isA<NearbyException>()
              .having((e) => e.message, 'message', isNot(contains('PlatformException')))
              .having((e) => e.detail, 'detail', contains('PlatformException')),
        ),
      );
    });
  });

  test('friendlyError never returns raw exception text', () {
    expect(friendlyError(StateError('boom')), isNot(contains('boom')));
    expect(friendlyError(TimeoutException('x')), contains('too long'));
  });

  test('scan-response short name maps to the full player name', () {
    expect(displayNameForAdvert('FS-44BD'), 'FreeSounder-44BD');
    expect(displayNameForAdvert(''), 'FreeSounder');
    expect(displayNameForAdvert('FreeSounder-44BD'), 'FreeSounder-44BD');
  });

  test('write chunk follows MTU with a 20-byte floor', () {
    expect(writeChunkSize(23), 20);
    expect(writeChunkSize(185), 182);
    expect(writeChunkSize(517), 182);
  });

  test('provision outcome comes from the RPC result alone', () {
    final ok = provisionResultFrom(['http://192.168.1.20:8090'], playerId: 'p');
    expect(ok.ok, isTrue);
    expect(ok.ip, '192.168.1.20');
    final bad = provisionResultFrom(['wrong_password']);
    expect(bad.ok, isFalse);
    expect(bad.reason, 'wrong_password');
    expect(provisionResultFrom(const []).reason, 'unable_to_connect');
  });

  test('wifi scan parsing skips hidden networks', () {
    final list = parseWifiScan(['Home', '-40', 'WPA2', '', '-80', 'NO', 'Cafe', '-70', 'NO']);
    expect(list.map((n) => n.ssid), ['Home', 'Cafe']);
    expect(list.last.isOpen, isTrue);
  });

  test('Improv errors map to friendly text', () {
    expect(friendlyImprovError(improvErrorUnableToConnect), contains('password'));
    expect(friendlyImprovError(0x7f), contains('Try again'));
  });

  test('Wi-Fi country comes from the phone locale, AU when missing', () {
    expect(wifiCountryFromLocale('en_AU'), 'AU');
    expect(wifiCountryFromLocale('en-NZ'), 'NZ');
    expect(wifiCountryFromLocale('de_DE.UTF-8'), 'DE');
    expect(wifiCountryFromLocale('en'), 'AU');
    expect(wifiCountryFromLocale('zh_Hant_TW'), 'TW');
  });

  test('player failure reason and detail are kept and mapped', () {
    final radio = provisionResultFrom(['radio_off', 'wlan0 is unavailable (rfkill blocked)']);
    expect(radio.ok, isFalse);
    expect(radio.reason, 'radio_off');
    expect(radio.detail, contains('rfkill'));
    expect(radio.message, contains('radio'));
    expect(provisionResultFrom(['wrong_password', '']).detail, isNull);
    expect(provisionResultFrom(['invalid_password']).message, contains('8 to 63'));
    expect(provisionResultFrom(['no_address']).message, contains('address'));
    expect(provisionResultFrom(['timeout']).message, contains('in time'));
  });
}

