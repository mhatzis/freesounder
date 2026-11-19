import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/models/bluetooth_output.dart';
import 'package:freesounder/ui/bluetooth_output_section.dart';

const speaker = BtSpeaker(mac: 'AA:BB:CC:DD:EE:01', name: 'Patio Boom');

class FakeApi implements BtOutputApi {
  FakeApi(this.current);

  BtOutputStatus current;
  final calls = <String>[];

  @override
  Future<BtOutputStatus> status() async => current;

  @override
  Future<List<BtSpeaker>> scan() async {
    calls.add('scan');
    return [speaker];
  }

  @override
  Future<BtOutputStatus> connect(BtSpeaker s) async {
    calls.add('connect ${s.mac}');
    return current = BtOutputStatus(
      available: true,
      enabled: true,
      active: true,
      codec: 'SBC',
      speaker: BtSpeaker(mac: s.mac, name: s.name, paired: true, connected: true),
    );
  }

  @override
  Future<BtOutputStatus> use({required bool enabled}) async {
    calls.add('use $enabled');
    return current = BtOutputStatus(available: true, enabled: enabled, speaker: current.speaker);
  }

  @override
  Future<BtOutputStatus> latency(int ms) async {
    calls.add('latency $ms');
    return current = BtOutputStatus(
      available: true,
      enabled: current.enabled,
      active: current.active,
      speaker: current.speaker,
      latencyMs: ms,
    );
  }

  @override
  Future<BtOutputStatus> forget() async {
    calls.add('forget');
    return current = const BtOutputStatus(available: true);
  }
}

Future<void> pump(WidgetTester tester, FakeApi api) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: BluetoothOutputSection(api: api, refreshEvery: const Duration(hours: 1)),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('no adapter: the section stays, says why, and can retry', (tester) async {
    // 0.6.12+btexp: the section used to vanish (SizedBox.shrink) whenever the
    // player reported available:false, so "bt setup is no longer there".
    final api = FakeApi(const BtOutputStatus(reason: 'No Bluetooth adapter found on this player'));
    await pump(tester, api);
    expect(find.byKey(const Key('bt-unavailable')), findsOneWidget);
    expect(find.text('Output'), findsOneWidget);
    expect(find.byKey(const Key('bt-experimental-badge')), findsOneWidget);
    expect(find.text('No Bluetooth adapter found on this player'), findsOneWidget);
    expect(find.byKey(const Key('bt-scan-button')), findsNothing);

    // The player brought its adapter up: Try again shows the full section.
    api.current = const BtOutputStatus(available: true);
    await tester.tap(find.byKey(const Key('bt-retry')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('bt-unavailable')), findsNothing);
    expect(find.byKey(const Key('bt-output-section')), findsOneWidget);
  });

  test('status reads the reason', () {
    final s = BtOutputStatus.fromJson({'available': false, 'reason': 'BlueZ not reachable: x'});
    expect(s.available, isFalse);
    expect(s.reason, 'BlueZ not reachable: x');
  });

  testWidgets('find, pick and use a speaker, then adjust the delay', (tester) async {
    final api = FakeApi(const BtOutputStatus(available: true));
    await pump(tester, api);
    expect(find.text('Experimental'), findsOneWidget);
    expect(find.text('Find speakers'), findsOneWidget);
    expect(find.byKey(const Key('bt-latency-slider')), findsNothing);

    await tester.tap(find.byKey(const Key('bt-scan-button')));
    await tester.pumpAndSettle();
    expect(find.text('Patio Boom'), findsOneWidget);

    await tester.tap(find.byKey(const Key('bt-found-AA:BB:CC:DD:EE:01')));
    await tester.pumpAndSettle();
    expect(api.calls, ['scan', 'connect AA:BB:CC:DD:EE:01']);
    expect(find.text('Playing · SBC'), findsOneWidget);
    expect(find.text('200 ms'), findsOneWidget);

    await tester.drag(find.byKey(const Key('bt-latency-slider')), const Offset(200, 0));
    await tester.pumpAndSettle();
    expect(api.calls.last, startsWith('latency '));
    expect(api.current.latencyMs, greaterThan(200));
  });

  testWidgets('switch back to wired and forget', (tester) async {
    final api = FakeApi(
      const BtOutputStatus(
        available: true,
        enabled: true,
        speaker: BtSpeaker(mac: 'AA:BB:CC:DD:EE:01', name: 'Patio Boom', paired: true),
      ),
    );
    await pump(tester, api);
    expect(find.text('Not connected — wired output plays until it is back'), findsOneWidget);
    await tester.tap(find.byKey(const Key('bt-output-wired')));
    await tester.pumpAndSettle();
    expect(api.calls, ['use false']);
    expect(find.text('Paired, using the wired output'), findsOneWidget);

    await tester.tap(find.byKey(const Key('bt-forget-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('bt-forget-confirm')));
    await tester.pumpAndSettle();
    expect(api.calls.last, 'forget');
    expect(find.text('Find speakers'), findsOneWidget);
  });

  testWidgets('warns about the shared radio and shows player errors', (tester) async {
    await pump(
      tester,
      FakeApi(
        const BtOutputStatus(
          available: true,
          warning: "This player's Wi-Fi and Bluetooth share one 2.4 GHz radio.",
          lastError: 'connect: org.bluez.Error.Failed page-timeout',
        ),
      ),
    );
    expect(find.byKey(const Key('bt-radio-warning')), findsOneWidget);
    expect(find.textContaining('share one 2.4 GHz radio'), findsOneWidget);
    expect(find.textContaining('page-timeout'), findsOneWidget);
  });

  test('status json', () {
    final s = BtOutputStatus.fromJson({
      'available': true,
      'enabled': true,
      'active': false,
      'speaker': {'mac': 'AA:BB:CC:DD:EE:01', 'name': null, 'paired': true, 'connected': true},
      'latency_ms': 180,
      'warning': null,
    });
    expect(s.speaker!.name, 'AA:BB:CC:DD:EE:01');
    expect(s.latencyMs, 180);
    expect(s.summary, 'Connected, starting…');
  });
}
