import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/theme/tokens.dart';
import 'package:freesounder/ui/app_shell.dart';
import 'package:freesounder/ui/rooms_panel.dart';

void main() {
  testWidgets('rooms panel lists mock rooms and can select one', (tester) async {
    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(home: Scaffold(body: RoomsPanel())),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Kitchen'), findsOneWidget);
    expect(find.text('Lounge'), findsOneWidget);
    expect(find.text('Bedroom'), findsOneWidget);
    expect(find.text('Mock data'), findsOneWidget);

    await tester.tap(find.text('Lounge'));
    await tester.pumpAndSettle();

    final container = ProviderScope.containerOf(
      tester.element(find.byType(RoomsPanel)),
    );
    expect(container.read(playbackProvider).activeRoomId, 'Lounge'.toLowerCase());
    expect(container.read(playbackProvider).activeRoomId, 'lounge');
  });

  testWidgets('narrow shell shows mini-player rooms chip and sheet', (tester) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(home: AppShell()),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Morning Light'), findsWidgets);
    expect(find.text('Kitchen'), findsWidgets);
    expect(find.byType(RoomsBottomSheet), findsOneWidget);

    await tester.tap(find.byKey(const Key('rooms-chip')));
    await tester.pumpAndSettle();
    expect(find.text('Lounge'), findsWidgets);
  });

  testWidgets('wide shell shows a right-hand rooms rail', (tester) async {
    tester.view.physicalSize = const Size(FsTokens.railBreakpoint + 80, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(home: AppShell()),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byType(RoomsPanel), findsOneWidget);
    expect(find.byType(RoomsBottomSheet), findsNothing);
    expect(find.text('Lounge'), findsOneWidget);
  });
}
