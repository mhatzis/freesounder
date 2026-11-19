import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/control_client.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/app_shell.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

// What the player says about Bluetooth for the room (per test).
var btStatus = <String, dynamic>{};

MockClient player() => MockClient((r) async {
  switch (r.url.path) {
    case '/api/v2/rooms':
      return http.Response(
        jsonEncode([
          {
            'id': 'patio',
            'name': 'Patio',
            'member_ids': ['patio'],
            'volume': 20,
            'source': 'radio',
            'url': 'http://10.0.0.2:8080',
          },
        ]),
        200,
      );
    case '/api/v2/now-playing':
      return http.Response(
        jsonEncode({'player_id': 'patio', 'source': 'radio', 'state': 'playing', 'title': 'Song'}),
        200,
      );
    case '/api/v2/rooms/patio/settings':
      return http.Response(
        jsonEncode({'name': 'Patio', 'volume_limit': 100, 'bass': 0, 'treble': 0, 'balance': 0, 'dac': 'auto'}),
        200,
      );
    case '/api/v2/rooms/patio/bluetooth':
      return http.Response(jsonEncode(btStatus), 200);
    case '/health':
      return http.Response(jsonEncode({'status': 'ok', 'version': '0.6.12+btexp'}), 200);
    case '/api/v2/info':
      return http.Response(jsonEncode({'state': 'ready', 'household_id': 'h'}), 200);
  }
  return http.Response('{"detail":"Not Found"}', 404);
});

class C extends ControlApiClient {
  C(String url) : super(baseUrl: url, httpClient: player());
  @override
  Stream<Map<String, dynamic>> connectEvents() => StreamController<Map<String, dynamic>>().stream;
}

Future<void> openRoomSettings(WidgetTester tester) async {
  tester.view.physicalSize = const Size(500, 2400);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final container = ProviderContainer(
    overrides: [
      appConfigProvider.overrideWithValue(const AppConfig(discoveryTimeout: Duration.zero)),
      controlClientFactoryProvider.overrideWithValue((url) => C(url)),
    ],
  );
  addTearDown(container.dispose);
  await tester.runAsync(() async {
    container.read(playbackProvider);
    await container.read(playbackProvider.notifier).connectTo('http://10.0.0.2:8080', persist: false);
  });
  await tester.pumpWidget(
    UncontrolledProviderScope(
      container: container,
      child: const MaterialApp(home: AppShell()),
    ),
  );
  await tester.pump(const Duration(seconds: 1));
  await tester.tap(find.byKey(const Key('now-playing-room-settings')));
  for (var i = 0; i < 20; i++) {
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
    await tester.pump(const Duration(milliseconds: 100));
  }
}

void main() {
  testWidgets('room settings on a BT build: speaker section when the player has an adapter', (tester) async {
    btStatus = {'experimental': true, 'available': true, 'enabled': false, 'active': false, 'latency_ms': 200};
    await openRoomSettings(tester);
    expect(find.byKey(const Key('bt-output-section')), findsOneWidget);
  });

  testWidgets('room settings on a BT build: no adapter still shows the section and why', (tester) async {
    btStatus = {
      'experimental': true,
      'available': false,
      'reason': 'No Bluetooth adapter found on this player (radio off or controller missing)',
      'enabled': false,
      'active': false,
    };
    await openRoomSettings(tester);
    expect(find.byKey(const Key('bt-unavailable')), findsOneWidget);
    expect(find.textContaining('No Bluetooth adapter found'), findsOneWidget);
    expect(find.byKey(const Key('bt-retry')), findsOneWidget);
  });
}
