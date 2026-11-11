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
    await tester.pumpAndSettle();
    final container = ProviderScope.containerOf(tester.element(find.byType(Scaffold).first));
    final np = container.read(playbackProvider).nowPlaying;
    expect(np?.title ?? np?.album ?? '', isNotEmpty);
  });
}
