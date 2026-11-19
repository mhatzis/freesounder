import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/mock_data.dart';
import 'package:freesounder/models/now_playing.dart';
import 'package:freesounder/models/player_settings.dart';
import 'package:freesounder/models/room.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/theme/tokens.dart';
import 'package:freesounder/ui/rooms_panel.dart';
import 'package:freesounder/ui/settings_screen.dart';

MockHousehold household(List<Room> rooms) {
  final m = MockHousehold();
  m.rooms = List.of(rooms);
  m.nowPlaying = {for (final r in rooms) r.id: NowPlaying(playerId: r.id)};
  m.settings = {for (final r in rooms) r.id: PlayerSettings(name: r.name)};
  return m;
}

const grouped = [
  Room(
    id: 'office',
    name: 'Office',
    volume: 40,
    state: 'playing',
    groupId: 'patio',
    memberIds: ['patio', 'office'],
    groupName: 'Patio + 1',
    relayingFor: 'Patio',
  ),
  Room(
    id: 'patio',
    name: 'Patio by the barbecue and the long garden wall',
    volume: 35,
    state: 'playing',
    groupId: 'patio',
    memberIds: ['patio', 'office'],
    groupName: 'Patio by the barbecue and the long garden wall + 1',
  ),
  Room(id: 'den', name: 'Den', volume: 20, available: false),
];

Future<ProviderContainer> pumpRooms(WidgetTester tester, double width, List<Room> rooms) async {
  tester.view.physicalSize = Size(width, 760);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        appConfigProvider.overrideWithValue(const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero)),
        mockHouseholdProvider.overrideWithValue(() => household(rooms)),
      ],
      child: MaterialApp(theme: FsTokens.light(), home: const Scaffold(body: RoomsPanel())),
    ),
  );
  await tester.pumpAndSettle();
  expect(tester.takeException(), isNull);
  return ProviderScope.containerOf(tester.element(find.byType(RoomsPanel)));
}

void main() {
  for (final width in [360.0, 390.0]) {
    testWidgets('grouped room rows never squeeze the name to vertical text at $width px', (tester) async {
      await pumpRooms(tester, width, grouped);
      final lineHeight = tester.getSize(find.byKey(const Key('room-name-den'))).height;
      for (final id in ['office', 'patio']) {
        final name = tester.getSize(find.byKey(Key('room-name-$id')));
        expect(name.width, greaterThan(80), reason: '$id title has room');
        expect(name.height, lessThanOrEqualTo(lineHeight + 0.5), reason: '$id title is one line');
        final status = tester.getSize(find.byKey(Key('room-status-$id')));
        expect(status.height, lessThanOrEqualTo(lineHeight * 2.2), reason: 'status wraps to at most 2 lines');
      }
      expect(find.textContaining('Playing for Patio'), findsOneWidget);
      final office = tester.widget<Text>(find.byKey(const Key('room-name-office')));
      expect(office.maxLines, 1);
      expect(office.overflow, TextOverflow.ellipsis);
    });
  }

  testWidgets('an offline room shows "Offline" (not vanishing) and can be removed', (tester) async {
    final container = await pumpRooms(tester, 390, grouped);
    expect(tester.widget<Text>(find.byKey(const Key('room-status-den'))).data, 'Offline');
    expect(find.byKey(const Key('room-settings-den')), findsNothing);
    await tester.tap(find.byKey(const Key('room-forget-den')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('confirm-forget-room')));
    await tester.pumpAndSettle();
    expect(container.read(playbackProvider).rooms.any((r) => r.id == 'den'), isFalse);
  });

  testWidgets('10 rooms at 360 px: the last one scrolls clear of the mini player', (tester) async {
    final rooms = [
      for (var i = 0; i < 10; i++)
        Room(id: 'r$i', name: 'Room number $i with a long name', volume: 30, state: 'idle'),
    ];
    await pumpRooms(tester, 360, rooms);
    final list = tester.widget<ListView>(find.byKey(const Key('rooms-list')));
    final pad = list.padding!.resolve(TextDirection.ltr);
    expect(pad.bottom, greaterThanOrEqualTo(FsTokens.miniPlayerHeight));
    await tester.scrollUntilVisible(find.byKey(const Key('room-name-r9')), 200);
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('room-name-r9')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('settings hides the player address unless developer mode is on', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          appConfigProvider.overrideWithValue(const AppConfig(forceDemo: false, discoveryTimeout: Duration.zero)),
        ],
        child: const MaterialApp(home: SettingsScreen()),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('Connected to'), findsNothing);
    await tester.scrollUntilVisible(
      find.byKey(const Key('reset-help-tile')),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.byKey(const Key('reset-help-tile')));
    await tester.pumpAndSettle();
    expect(find.textContaining('power button 5 times'), findsOneWidget);
    expect(find.textContaining('Factory reset at the bottom'), findsOneWidget);
    expect(find.textContaining('GPIO3'), findsNothing);
  });
}
