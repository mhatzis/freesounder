import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/theme/tokens.dart';
import 'package:freesounder/ui/app_shell.dart';
import 'package:freesounder/ui/rooms_panel.dart';
import 'package:freesounder/ui/settings_screen.dart';

Widget _host(Widget child) {
  return ProviderScope(
    overrides: [
      appConfigProvider.overrideWithValue(
        const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero),
      ),
    ],
    child: MaterialApp(home: child),
  );
}

Widget _liveHost(Widget child) {
  return ProviderScope(
    overrides: [
      appConfigProvider.overrideWithValue(
        const AppConfig(forceDemo: false, discoveryTimeout: Duration.zero),
      ),
    ],
    child: MaterialApp(home: child),
  );
}

void main() {

  testWidgets('rooms panel lists mock rooms and can select one', (tester) async {
    await tester.pumpWidget(
      _host(const Scaffold(body: RoomsPanel())),
    );
    await tester.pumpAndSettle();

    expect(find.text('Kitchen'), findsOneWidget);
    expect(find.text('Lounge'), findsOneWidget);
    expect(find.text('Bedroom'), findsOneWidget);
    expect(find.text('Demo mode'), findsOneWidget);

    await tester.tap(find.text('Lounge'));
    await tester.pumpAndSettle();

    final container = ProviderScope.containerOf(
      tester.element(find.byType(RoomsPanel)),
    );
    expect(container.read(playbackProvider).activeRoomId, 'lounge');
  });

  testWidgets('narrow shell shows mini-player rooms chip and sheet', (tester) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host(const AppShell()));
    await tester.pumpAndSettle();

    expect(find.text('Morning Light'), findsWidgets);
    expect(find.text('Kitchen'), findsWidgets);
    expect(find.byType(RoomsBottomSheet), findsOneWidget);
    expect(find.text('FreeSounder'), findsOneWidget);
    expect(find.text('Demo mode'), findsWidgets);
    expect(find.byKey(const Key('settings-button')), findsOneWidget);

    await tester.tap(find.byKey(const Key('rooms-chip')));
    await tester.pumpAndSettle();
    expect(find.text('Lounge'), findsWidgets);
  });

  testWidgets('wide shell shows a right-hand rooms rail', (tester) async {
    tester.view.physicalSize = const Size(FsTokens.railBreakpoint + 80, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host(const AppShell()));
    await tester.pumpAndSettle();

    expect(find.byType(RoomsPanel), findsOneWidget);
    expect(find.byType(RoomsBottomSheet), findsNothing);
    expect(find.text('Lounge'), findsOneWidget);
    expect(find.text('FreeSounder'), findsOneWidget);
  });

  testWidgets('settings screen can stay in demo and shows connect field', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host(const SettingsScreen()));
    await tester.pumpAndSettle();

    expect(find.text('Demo household'), findsOneWidget);
    expect(find.byKey(const Key('host-address-field')), findsOneWidget);
    expect(find.byKey(const Key('demo-switch')), findsOneWidget);
    expect(find.text('Add a player'), findsOneWidget);
    final demoSwitch = tester.widget<SwitchListTile>(find.byKey(const Key('demo-switch')));
    expect(demoSwitch.value, isTrue);
  });

  testWidgets('room settings sheet shows tone controls', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host(const AppShell()));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('now-playing-room-settings')));
    await tester.pumpAndSettle();

    expect(find.text('Room settings'), findsOneWidget);
    expect(find.byKey(const Key('room-name-field')), findsOneWidget);
    expect(find.byKey(const Key('bass-slider')), findsOneWidget);
    expect(find.byKey(const Key('treble-slider')), findsOneWidget);
    expect(find.byKey(const Key('balance-slider')), findsOneWidget);
    expect(find.byKey(const Key('loudness-switch')), findsOneWidget);
    expect(find.text('Bass boost'), findsOneWidget);
  });

  testWidgets('add player wizard lists a demo Pi and can adopt it', (tester) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host(const AppShell()));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('rooms-chip')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('add-player-button')));
    await tester.pumpAndSettle();

    expect(find.text('Add a player'), findsOneWidget);
    expect(find.byKey(const Key('unclaimed-attic-pi')), findsOneWidget);

    await tester.tap(find.byKey(const Key('unclaimed-attic-pi')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('new-room-name')), 'Attic');
    await tester.tap(find.byKey(const Key('join-household')));
    await tester.pumpAndSettle();

    final container = ProviderScope.containerOf(
      tester.element(find.byType(AppShell)),
    );
    expect(container.read(playbackProvider).rooms.map((room) => room.id), contains('attic-pi'));
    expect(container.read(playbackProvider).rooms.map((room) => room.name), contains('Attic'));
  });

  testWidgets('without a player, shows empty setup state not demo', (tester) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_liveHost(const AppShell()));
    await tester.pumpAndSettle();

    expect(find.text('Set up your first player'), findsWidgets);
    expect(find.byKey(const Key('setup-add-player')), findsOneWidget);
    expect(find.byKey(const Key('setup-enter-address')), findsOneWidget);
    expect(find.byKey(const Key('setup-try-demo')), findsOneWidget);
    expect(find.text('Demo'), findsNothing);
    expect(find.text('Morning Light'), findsNothing);

    await tester.tap(find.byKey(const Key('setup-try-demo')));
    await tester.pumpAndSettle();
    expect(find.text('Morning Light'), findsWidgets);
    expect(find.text('Demo'), findsWidgets);
  });

  testWidgets('developer SSH is hidden until version is tapped seven times', (tester) async {
    tester.view.physicalSize = const Size(400, 1000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host(const AppShell()));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('now-playing-room-settings')));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.byKey(const Key('player-version-tap')));
    expect(find.text('FreeSounder 0.5.3'), findsOneWidget);
    expect(find.byKey(const Key('ssh-developer-switch')), findsNothing);

    for (var i = 0; i < 7; i++) {
      await tester.tap(find.byKey(const Key('player-version-tap')));
      await tester.pump();
    }
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('ssh-developer-switch')), findsOneWidget);
    await tester.tap(find.byKey(const Key('ssh-developer-switch')));
    await tester.pumpAndSettle();
    expect(find.textContaining('demo-ssh-password'), findsOneWidget);
    expect(find.textContaining('ssh developer@freesounder-demo.local'), findsOneWidget);
  });
}
