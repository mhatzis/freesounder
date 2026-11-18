import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/mock_data.dart';
import 'package:freesounder/models/now_playing.dart';
import 'package:freesounder/models/player_settings.dart';
import 'package:freesounder/models/room.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/rooms_panel.dart';

const names = ['Kitchen', 'Lounge', 'Office', 'Bedroom', 'Patio', 'Study', 'Garage', 'Bath', 'Hall', 'Den'];

MockHousehold tenRooms() {
  final m = MockHousehold();
  m.rooms = [for (final n in names) Room(id: n.toLowerCase(), name: n, volume: 30, state: 'idle')];
  m.nowPlaying = {for (final n in names) n.toLowerCase(): NowPlaying(playerId: n.toLowerCase())};
  m.settings = {for (final n in names) n.toLowerCase(): PlayerSettings(name: n)};
  return m;
}

void main() {
  testWidgets('a 10-room home: every room listed, all 10 grouped on a phone-sized screen', (tester) async {
    tester.view.physicalSize = const Size(390, 700);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          appConfigProvider.overrideWithValue(const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero)),
          mockHouseholdProvider.overrideWithValue(tenRooms),
        ],
        child: const MaterialApp(home: Scaffold(body: RoomsPanel())),
      ),
    );
    await tester.pumpAndSettle();
    final container = ProviderScope.containerOf(tester.element(find.byType(RoomsPanel)));
    expect(container.read(playbackProvider).rooms, hasLength(10));
    // The list scrolls to the 10th room.
    await tester.scrollUntilVisible(find.text('Den'), 100, scrollable: find.byType(Scrollable).first);
    expect(find.text('Den'), findsOneWidget);
    await tester.drag(find.byType(Scrollable).first, const Offset(0, 2000));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('room-group-kitchen')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('group-all')));
    await tester.pump();
    expect(find.textContaining('Kitchen + 9'), findsOneWidget);
    // Done is still on screen with 9 other rooms in the sheet.
    expect(tester.takeException(), isNull);
    final done = find.byKey(const Key('group-apply'));
    expect(tester.getRect(done).bottom, lessThanOrEqualTo(700));
    await tester.tap(done);
    await tester.pumpAndSettle();
    final rooms = container.read(playbackProvider).rooms;
    expect(rooms.every((r) => r.groupName == 'Kitchen + 9'), isTrue, reason: rooms.map((r) => r.groupName).join(','));
  });
}
