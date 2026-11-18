import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/radio_screen.dart';

void main() {
  testWidgets('radio screen lists stations and plays one in demo', (tester) async {
    tester.view.physicalSize = const Size(500, 1000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          appConfigProvider.overrideWithValue(const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero)),
        ],
        child: MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(onPressed: () => showRadioScreen(context), child: const Text('open')),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('radio-search')), 'demo');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(find.text('Demo FM'), findsOneWidget);
    await tester.tap(find.byKey(const Key('radio-station-demo-fm')));
    // The live equaliser animates forever, so pump frames instead of settling.
    for (var i = 0; i < 10; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
    final container = ProviderScope.containerOf(tester.element(find.byType(Scaffold).first));
    final np = container.read(playbackProvider).nowPlaying;
    expect(np?.title ?? np?.album ?? '', isNotEmpty);
    expect(find.byKey(const Key('radio-live')), findsOneWidget, reason: 'the playing station shows the live bars');
  });

  testWidgets('genre and country chips filter the search', (tester) async {
    tester.view.physicalSize = const Size(4000, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          appConfigProvider.overrideWithValue(const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero)),
        ],
        child: const MaterialApp(home: RadioScreen()),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Demo FM'), findsOneWidget);
    expect(find.text('Example Jazz'), findsOneWidget);
    await tester.tap(find.byKey(const Key('radio-chip-tag-jazz')));
    await tester.pumpAndSettle();
    expect(find.text('Demo FM'), findsNothing);
    expect(find.text('Example Jazz'), findsOneWidget);
    await tester.tap(find.byKey(const Key('radio-chip-tag-jazz')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('radio-chip-country-AU')));
    await tester.pumpAndSettle();
    expect(find.text('Demo FM'), findsOneWidget);
    expect(find.text('Example Jazz'), findsNothing);
  });
}
