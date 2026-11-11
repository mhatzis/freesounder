import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/ble_steps.dart';
import 'package:freesounder/api/connection_prefs.dart';
import 'package:freesounder/api/nearby.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/add_player_sheet.dart';

Widget _host({NearbyTransport? transport, String? phoneSsid, bool developer = false, String? wifiCountry}) {
  return ProviderScope(
    overrides: [
      if (developer) developerModeProvider.overrideWith(_DevOn.new),
      appConfigProvider.overrideWithValue(
        const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero),
      ),
      if (transport != null) nearbyTransportProvider.overrideWithValue(transport),
    ],
    child: MaterialApp(
      home: Scaffold(
        body: AddPlayerSheet(
          transport: transport ?? DemoNearbyTransport(delay: Duration.zero),
          phoneSsid: phoneSsid ?? 'Home',
          wifiCountry: wifiCountry,
        ),
      ),
    ),
  );
}

void main() {
  testWidgets('find step lists a demo BLE player and an online unclaimed player', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host());
    await tester.pumpAndSettle();

    expect(find.text('Find nearby players'), findsOneWidget);
    expect(find.text('FreeSounder-A1B2'), findsOneWidget);
    expect(find.text('freesounder-attic'), findsOneWidget);
    expect(find.byKey(const Key('nearby-demo-ble-porch')), findsOneWidget);
    expect(find.byKey(const Key('unclaimed-attic-pi')), findsOneWidget);
  });

  testWidgets('online unclaimed player skips Wi-Fi and joins the household', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host());
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('unclaimed-attic-pi')));
    await tester.pumpAndSettle();

    expect(find.text('Join this system'), findsOneWidget);
    expect(find.byKey(const Key('new-room-name')), findsOneWidget);
    await tester.enterText(find.byKey(const Key('new-room-name')), 'Attic');
    await tester.tap(find.byKey(const Key('join-household')));
    await tester.pumpAndSettle();
    // Sheet pops on success, leaving the empty scaffold.
    expect(find.byKey(const Key('join-household')), findsNothing);
  });

  testWidgets('BLE player walks through Wi-Fi, password, progress, then adopt', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host());
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('nearby-demo-ble-porch')));
    await tester.pumpAndSettle();

    expect(find.text('Wi-Fi network'), findsOneWidget);
    expect(find.text('Home'), findsWidgets);
    expect(find.text('Cafe'), findsOneWidget);
    await tester.tap(find.byKey(const Key('wifi-continue')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('wifi-password')), findsOneWidget);
    await tester.enterText(find.byKey(const Key('wifi-password')), 'secret');
    await tester.enterText(find.byKey(const Key('provision-room-name')), 'Porch');
    await tester.tap(find.byKey(const Key('wifi-join')));
    await tester.pumpAndSettle();

    expect(find.text('Join this system'), findsOneWidget);
    expect(find.text('Porch'), findsWidgets);
    await tester.tap(find.byKey(const Key('join-household')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('join-household')), findsNothing);
  });

  testWidgets('wrong password stays on progress with a retry path', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      _host(transport: DemoNearbyTransport(delay: Duration.zero, failReason: 'wrong_password')),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('nearby-demo-ble-porch')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('wifi-continue')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('wifi-password')), 'nope');
    await tester.tap(find.byKey(const Key('wifi-join')));
    await tester.pumpAndSettle();

    expect(find.textContaining('Wrong password'), findsWidgets);
    expect(find.byKey(const Key('wifi-retry')), findsOneWidget);
  });

  testWidgets('radio not ready shows friendly text and still lists online players', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host(transport: _FailingTransport(failScan: true)));
    await tester.pumpAndSettle();

    expect(find.textContaining('Bluetooth is not ready yet'), findsOneWidget);
    expect(find.textContaining('PlatformException'), findsNothing);
    expect(find.byKey(const Key('unclaimed-attic-pi')), findsOneWidget);
    expect(find.byKey(const Key('ble-debug-line')), findsNothing);
  });

  testWidgets('a silent player fails at a named step with retry, no endless spinner', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final transport = _FailingTransport();
    await tester.pumpWidget(_host(transport: transport, developer: true));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('nearby-demo-ble-porch')));
    await tester.pumpAndSettle();

    expect(find.textContaining('stopped answering'), findsOneWidget);
    expect(find.byKey(const Key('retry-player')), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
    final debug = tester.widget<Text>(find.byKey(const Key('ble-debug-line')));
    expect(debug.data, contains('Reading player details'));
    expect(debug.data, contains('timed out'));

    transport.failInfo = false;
    await tester.tap(find.byKey(const Key('retry-player')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('wifi-continue')), findsOneWidget);
  });

  testWidgets('sends the Wi-Fi country, offers rescan on an empty list, shows the player reason',
      (tester) async {
    tester.view.physicalSize = const Size(400, 1000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final transport = _RadioOffTransport();
    await tester.pumpWidget(_host(transport: transport, wifiCountry: 'au', phoneSsid: ''));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('nearby-demo-ble-porch')));
    await tester.pumpAndSettle();

    expect(transport.countries, ['AU']);
    expect(find.byKey(const Key('wifi-empty')), findsOneWidget);
    await tester.tap(find.byKey(const Key('wifi-rescan')));
    await tester.pumpAndSettle();
    expect(transport.scans, 2);
    expect(find.byKey(const Key('wifi-Home')), findsOneWidget);

    await tester.tap(find.byKey(const Key('wifi-continue')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('wifi-password')), 'password123');
    await tester.tap(find.byKey(const Key('wifi-join')));
    await tester.pumpAndSettle();

    expect(find.textContaining('Wi-Fi radio is switched off'), findsOneWidget);
    expect(find.textContaining('Player said: wlan0 is unavailable'), findsOneWidget);
    expect(find.byKey(const Key('wifi-retry')), findsOneWidget);
  });
}

class _RadioOffTransport extends DemoNearbyTransport {
  _RadioOffTransport() : super(delay: Duration.zero);

  final countries = <String>[];
  var scans = 0;

  @override
  Future<bool> setWifiCountry(String country) async {
    countries.add(country);
    return true;
  }

  @override
  Future<List<WifiCandidate>> scanWifi() async {
    scans += 1;
    if (scans == 1) {
      return const [];
    }
    return super.scanWifi();
  }

  @override
  Future<ProvisionResult> provision({required String ssid, required String password, String? roomName}) async {
    return const ProvisionResult(ok: false, reason: 'radio_off', detail: 'wlan0 is unavailable (rfkill blocked)');
  }
}

class _DevOn extends DeveloperModeNotifier {
  @override
  bool build() => true;
}

class _FailingTransport extends DemoNearbyTransport {
  _FailingTransport({this.failScan = false}) : super(delay: Duration.zero);

  final bool failScan;
  bool failInfo = true;

  @override
  Future<void> startScan() async {
    if (failScan) {
      throw const NearbyException(
        BleStep.radio,
        'Bluetooth is not ready yet. Wait a moment and search again.',
        detail: 'PlatformException(startScan, CBManagerStateUnknown)',
      );
    }
    await super.startScan();
  }

  @override
  Future<DeviceInfo> deviceInfo() async {
    if (failInfo) {
      throw NearbyException(
        BleStep.deviceInfo,
        friendlyStepMessage(BleStep.deviceInfo, timedOut: true),
        detail: 'timed out after 10s',
      );
    }
    return super.deviceInfo();
  }
}
