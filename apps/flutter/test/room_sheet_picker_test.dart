import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/app_shell.dart';
import 'package:freesounder/ui/audio_board_picker.dart';

Widget _host(Widget child) => ProviderScope(
  overrides: [appConfigProvider.overrideWithValue(const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero))],
  child: MaterialApp(home: child),
);

Future<void> _openSheet(WidgetTester tester) async {
  tester.view.physicalSize = const Size(400, 860);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(_host(const AppShell()));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const Key('now-playing-room-settings')));
  await tester.pumpAndSettle();
  expect(find.text('Room settings'), findsOneWidget);
}

void main() {
  testWidgets('X closes the room settings sheet', (tester) async {
    await _openSheet(tester);
    await tester.tap(find.byKey(const Key('sheet-close')));
    await tester.pumpAndSettle();
    expect(find.text('Room settings'), findsNothing);
  });

  testWidgets('dragging the handle down closes it even when the list is scrolled', (tester) async {
    await _openSheet(tester);
    await tester.drag(find.byKey(const Key('room-settings-scroll')), const Offset(0, -300));
    await tester.pumpAndSettle();
    await tester.drag(find.byKey(const Key('sheet-drag-handle')), const Offset(0, 500));
    await tester.pumpAndSettle();
    expect(find.text('Room settings'), findsNothing);
  });

  testWidgets('a short handle drag springs back', (tester) async {
    await _openSheet(tester);
    await tester.timedDrag(
      find.byKey(const Key('sheet-drag-handle')),
      const Offset(0, 60),
      const Duration(milliseconds: 600),
    );
    await tester.pumpAndSettle();
    expect(find.text('Room settings'), findsOneWidget);
  });

  testWidgets('pulling the list down from its top closes it', (tester) async {
    await _openSheet(tester);
    await tester.fling(find.byKey(const Key('room-name-field')), const Offset(0, 600), 2000);
    await tester.pumpAndSettle();
    expect(find.text('Room settings'), findsNothing);
  });

  testWidgets('back button closes it', (tester) async {
    await _openSheet(tester);
    final nav = tester.state<NavigatorState>(find.byType(Navigator).first);
    await nav.maybePop();
    await tester.pumpAndSettle();
    expect(find.text('Room settings'), findsNothing);
  });

  testWidgets('tapping outside closes it', (tester) async {
    await _openSheet(tester);
    await tester.tapAt(const Offset(200, 10));
    await tester.pumpAndSettle();
    expect(find.text('Room settings'), findsNothing);
  });

  testWidgets('audio board picker: grouped, one-line titles, search, check, pick', (tester) async {
    await _openSheet(tester);
    await tester.scrollUntilVisible(
      find.byKey(const Key('dac-picker-tile')),
      200,
      scrollable: find.byType(Scrollable).last,
    );
    expect(find.text('Auto (recommended)'), findsOneWidget);
    await tester.tap(find.byKey(const Key('dac-picker-tile')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('dac-picker')), findsOneWidget);
    expect(find.text('DAC boards'), findsOneWidget);
    // The current choice carries the check mark.
    expect(
      find.descendant(of: find.byKey(const Key('dac-option-auto')), matching: find.byIcon(Icons.check)),
      findsOneWidget,
    );

    await tester.enterText(find.byKey(const Key('dac-picker-search')), 'pifi');
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('dac-option-pifi-dacplus')), findsOneWidget);
    expect(find.byKey(const Key('dac-option-hifiberry-amp2')), findsNothing);
    expect(find.text('HiFiBerry DAC+ compatible (PCM5122, headphone jack)'), findsOneWidget);

    await tester.tap(find.byKey(const Key('dac-option-pifi-dacplus')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('dac-picker')), findsNothing);
    expect(find.text('PiFi DAC+ 2.0'), findsOneWidget);
    final container = ProviderScope.containerOf(tester.element(find.text('Room settings')));
    final roomId = container.read(playbackProvider).activeRoomId!;
    expect(container.read(playbackProvider).settings[roomId]?.dac, 'pifi-dacplus');
  });

  testWidgets('every picker row fits on screen without overlap', (tester) async {
    tester.view.physicalSize = const Size(360, 700);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: AudioBoardPicker(current: 'hifiberry-dacplus')),
      ),
    );
    await tester.pumpAndSettle();
    final rows = find.byType(ListTile);
    Rect? previous;
    for (final e in rows.evaluate()) {
      final box = e.renderObject! as RenderBox;
      final rect = box.localToGlobal(Offset.zero) & box.size;
      if (previous != null) {
        expect(rect.top, greaterThanOrEqualTo(previous.bottom - 0.5));
      }
      previous = rect;
    }
    expect(tester.takeException(), isNull);
  });

  test('every option has a group', () {
    for (final o in audioBoardOptions) {
      expect(audioBoardGroups, contains(o.group));
    }
    expect(audioBoardFor('nope').id, 'auto');
  });

  test('room audio lines show what was detected', () {
    final lines = describeRoomAudio({
      'output': {
        'reason': 'sound card sndrpihifiberry',
        'pcm': 'default:CARD=sndrpihifiberry',
        'detected': 'PCM512x/TAS5756 at 0x4d (auto-detected, dtoverlay=hifiberry-dacplus)',
        'cards': 'Headphones, vc4hdmi0, vc4hdmi1, sndrpihifiberry',
      },
    });
    expect(lines, contains('Detected: PCM512x/TAS5756 at 0x4d (auto-detected, dtoverlay=hifiberry-dacplus)'));
    expect(lines, contains('Sound cards: Headphones, vc4hdmi0, vc4hdmi1, sndrpihifiberry'));
  });

  testWidgets('factory reset asks first, in danger style', (tester) async {
    await _openSheet(tester);
    await tester.scrollUntilVisible(
      find.byKey(const Key('factory-reset-button')),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.byKey(const Key('factory-reset-button')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('factory-reset-dialog')), findsOneWidget);
    await tester.tap(find.byKey(const Key('factory-reset-cancel')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('factory-reset-dialog')), findsNothing);
    expect(find.text('Room settings'), findsOneWidget);
    await tester.tap(find.byKey(const Key('factory-reset-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('factory-reset-confirm')));
    await tester.pumpAndSettle();
    expect(find.text('Room settings'), findsNothing);
    expect(find.textContaining('Resetting the player'), findsOneWidget);
  });

  test('room audio lines show the output check and direct mode', () {
    final lines = describeRoomAudio({
      'output': {
        'pcm': 'hw:CARD=sndrpihifiberry,DEV=0',
        'reason': 'sound card sndrpihifiberry',
        'check': 'failed: aplay: boom',
        'direct': '1',
      },
    });
    expect(lines, contains('Output check: failed: aplay: boom'));
    expect(lines.any((l) => l.startsWith('Tone controls: off')), isTrue);
    expect(describeRoomAudio({'output': {'check': 'ok'}}), contains('Output check: OK'));
  });
}
