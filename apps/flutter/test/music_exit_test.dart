import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/sources_sheet.dart';

// "I can't get out of music services": every screen of the music flow has a
// way out (✕, back arrow, swipe down, tap outside, system back) and back
// from a source screen returns to the picker.

Future<void> _pump(WidgetTester tester, {EdgeInsets padding = EdgeInsets.zero}) async {
  tester.view.physicalSize = const Size(600, 1300);
  tester.view.devicePixelRatio = 1.0;
  tester.view.padding = FakeViewPadding(top: padding.top, bottom: padding.bottom);
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        appConfigProvider.overrideWithValue(const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero)),
      ],
      child: MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: Center(
              child: TextButton(onPressed: () => showSourcesSheet(context), child: const Text('open')),
            ),
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
  expect(find.byKey(const Key('sources-sheet')), findsOneWidget);
}

/// Spotify shows a spinner until its store loads, so never wait to "settle".
Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 12; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

Finder get _sheet => find.byKey(const Key('sources-sheet'));

Future<void> _systemBack(WidgetTester tester) async {
  // Android back button / iOS-style back gesture both go through popRoute.
  await tester.binding.handlePopRoute();
  await _settle(tester);
}

void main() {
  testWidgets('picker: the ✕ closes it', (tester) async {
    await _pump(tester);
    await tester.tap(find.byKey(const Key('sources-close')));
    await tester.pumpAndSettle();
    expect(_sheet, findsNothing);
  });

  testWidgets('picker: swipe down on the handle closes it', (tester) async {
    await _pump(tester);
    await tester.drag(find.byKey(const Key('sources-drag-handle')), const Offset(0, 600));
    await tester.pumpAndSettle();
    expect(_sheet, findsNothing);
  });

  testWidgets('picker: tap outside closes it', (tester) async {
    await _pump(tester);
    await tester.tapAt(const Offset(300, 5));
    await tester.pumpAndSettle();
    expect(_sheet, findsNothing);
  });

  testWidgets('picker: system back closes it', (tester) async {
    await _pump(tester);
    await _systemBack(tester);
    expect(_sheet, findsNothing);
  });

  testWidgets('picker stays below the iOS status bar / notch', (tester) async {
    await _pump(tester, padding: const EdgeInsets.only(top: 59, bottom: 34));
    final handle = tester.getTopLeft(find.byKey(const Key('sources-drag-handle')));
    expect(handle.dy, greaterThanOrEqualTo(59));
    final close = tester.getRect(find.byKey(const Key('sources-close')));
    expect(close.top, greaterThanOrEqualTo(59));
  });

  for (final (tile, title) in [
    ('source-radio', 'Radio'),
    ('source-library', 'Music library'),
    ('source-podcasts', 'Podcasts'),
    ('source-spotify', 'Spotify'),
  ]) {
    testWidgets('$title: back arrow returns to the picker, ✕ leaves the flow', (tester) async {
      await _pump(tester);
      await tester.tap(find.byKey(Key(tile)));
      await _settle(tester);
      expect(find.widgetWithText(AppBar, title), findsOneWidget);
      expect(find.byType(BackButton), findsOneWidget);
      expect(find.byKey(const Key('music-close')), findsOneWidget);

      await tester.tap(find.byType(BackButton));
      await _settle(tester);
      expect(_sheet, findsOneWidget, reason: 'back from $title returns to the picker');

      await tester.tap(find.byKey(Key(tile)));
      await _settle(tester);
      await _systemBack(tester);
      expect(_sheet, findsOneWidget, reason: 'system back from $title returns to the picker');

      await tester.tap(find.byKey(Key(tile)));
      await _settle(tester);
      await tester.tap(find.byKey(const Key('music-close')));
      await _settle(tester);
      expect(_sheet, findsNothing);
      expect(find.byType(AppBar), findsNothing);
      expect(find.text('open'), findsOneWidget);
    });
  }

  testWidgets('library sub-screens: back steps back one level, ✕ leaves the flow', (tester) async {
    await _pump(tester);
    await tester.tap(find.byKey(const Key('source-library')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('library-album-Blue Hour')));
    await tester.pumpAndSettle();
    expect(find.byType(BackButton), findsOneWidget);
    expect(find.byKey(const Key('music-close')), findsOneWidget);
    await tester.tap(find.byType(BackButton));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(AppBar, 'Music library'), findsOneWidget);

    await tester.tap(find.byKey(const Key('library-shares')));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(AppBar, 'Music shares'), findsOneWidget);
    await tester.tap(find.byKey(const Key('library-add-share')));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(AppBar, 'Add music'), findsOneWidget);
    expect(find.byKey(const Key('music-close')), findsOneWidget);
    await tester.tap(find.byKey(const Key('music-close')));
    await tester.pumpAndSettle();
    expect(find.byType(AppBar), findsNothing);
    expect(_sheet, findsNothing);
  });

  testWidgets('podcast show screen has back and ✕', (tester) async {
    await _pump(tester);
    await tester.tap(find.byKey(const Key('source-podcasts')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('podcast-search')), 'demo');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('podcast-https://example.com/demo.xml')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('podcast-subscribe')), findsOneWidget);
    expect(find.byType(BackButton), findsOneWidget);
    expect(find.byKey(const Key('music-close')), findsOneWidget);
    await tester.tap(find.byType(BackButton));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(AppBar, 'Podcasts'), findsOneWidget);
    await tester.tap(find.byKey(const Key('music-close')));
    await tester.pumpAndSettle();
    expect(find.byType(AppBar), findsNothing);
  });
}
