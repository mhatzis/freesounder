import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/nearby.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/add_player_sheet.dart';

Widget _host({NearbyTransport? transport, String? phoneSsid}) {
  return ProviderScope(
    overrides: [
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
}
