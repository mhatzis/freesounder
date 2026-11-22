import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/models/room.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/volume_sheet.dart';

Widget _host(Widget child) {
  return ProviderScope(
    overrides: [
      appConfigProvider.overrideWithValue(
        const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero),
      ),
    ],
    child: MaterialApp(home: Scaffold(body: child)),
  );
}

void main() {
  test('group level is the average and the room I am on comes first', () {
    const a = Room(id: 'a', name: 'A', volume: 40, memberIds: ['a', 'b']);
    const b = Room(id: 'b', name: 'B', volume: 20, memberIds: ['a', 'b']);
    const c = Room(id: 'c', name: 'C', volume: 90);
    final members = groupRooms([b, c, a], a);
    expect(members.map((r) => r.id), ['a', 'b']);
    expect(groupLevel(members), 30);
    expect(groupRooms([a, b, c], c).map((r) => r.id), ['c']);
  });

  testWidgets('a single room gets a slider; a group opens all rooms and each room', (tester) async {
    tester.view.physicalSize = const Size(500, 1000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(_host(const QuickVolume(roomId: 'kitchen')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('quick-volume')), findsOneWidget);
    expect(find.text('40'), findsOneWidget);

    final container = ProviderScope.containerOf(tester.element(find.byType(QuickVolume)));
    await container.read(playbackProvider.notifier).setGroupMembers('kitchen', ['kitchen', 'lounge']);
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('quick-volume')), findsNothing);
    expect(find.byKey(const Key('quick-volume-group')), findsOneWidget);
    expect(find.text('35'), findsOneWidget); // (40 + 30) / 2

    await tester.tap(find.byKey(const Key('quick-volume-group')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('volume-group')), findsOneWidget);
    expect(find.byKey(const Key('volume-room-kitchen')), findsOneWidget);
    expect(find.byKey(const Key('volume-room-lounge')), findsOneWidget);
    expect(find.text('All rooms'), findsOneWidget);
  });

  testWidgets('mute from the volume panel', (tester) async {
    await tester.pumpWidget(_host(const VolumeSheet(roomId: 'lounge')));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Mute Lounge'));
    await tester.pumpAndSettle();
    final container = ProviderScope.containerOf(tester.element(find.byType(VolumeSheet)));
    final lounge = container.read(playbackProvider).rooms.firstWhere((r) => r.id == 'lounge');
    expect(lounge.muted, isTrue);
    expect(find.byTooltip('Unmute Lounge'), findsOneWidget);
  });
}
