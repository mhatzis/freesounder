import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/spotify/ui/spotify_brand.dart';
import 'package:freesounder/ui/app_shell.dart';
import 'package:freesounder/ui/intro_splash.dart';
import 'package:freesounder/ui/station_colors.dart';

void main() {
  group('intro splash', () {
    testWidgets('plays once over the app with the caption, then gets out of the way', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: IntroSplash(child: Text('app'))));
      expect(find.byKey(const Key('intro-splash')), findsOneWidget);
      await tester.pump(const Duration(milliseconds: 1200));
      expect(find.text(introCaption), findsOneWidget);
      expect(find.text(introSubline), findsOneWidget);
      await tester.pump(const Duration(milliseconds: 1400));
      await tester.pump();
      expect(find.byKey(const Key('intro-splash')), findsNothing);
      expect(find.text('app'), findsOneWidget);
    });

    testWidgets('lasts 2.5 s with the caption fully readable for most of it', (tester) async {
      const intro = IntroSplash(child: SizedBox());
      expect(intro.duration, const Duration(milliseconds: 2500));
      final readable = intro.duration * (fadeStart - captionIn.$2);
      expect(readable, greaterThanOrEqualTo(const Duration(milliseconds: 1400)));
      await tester.pumpWidget(const MaterialApp(home: IntroSplash(child: Text('app'))));
      double captionOpacity() => tester
          .widget<Opacity>(find.ancestor(of: find.text(introCaption), matching: find.byType(Opacity)).first)
          .opacity;
      await tester.pump(const Duration(milliseconds: 900));
      expect(captionOpacity(), 1.0);
      await tester.pump(const Duration(milliseconds: 1300)); // 2.2 s: still on screen
      expect(find.byKey(const Key('intro-splash')), findsOneWidget);
      expect(captionOpacity(), 1.0);
    });

    testWidgets('a tap skips it at any point', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: IntroSplash(child: Text('app'))));
      await tester.pump(const Duration(milliseconds: 100));
      await tester.tap(find.byKey(const Key('intro-splash')));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 250));
      await tester.pump();
      expect(find.byKey(const Key('intro-splash')), findsNothing);
    });

    testWidgets('reduced motion or disabled: no intro at all', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: MediaQuery(data: MediaQueryData(disableAnimations: true), child: IntroSplash(child: Text('app'))),
        ),
      );
      expect(find.byKey(const Key('intro-splash')), findsNothing);
      await tester.pumpWidget(const MaterialApp(home: IntroSplash(enabled: false, child: Text('app'))));
      expect(find.byKey(const Key('intro-splash')), findsNothing);
    });
  });

  group('station colours', () {
    test('a vivid logo colour beats white and grey', () {
      final px = <int>[];
      for (var i = 0; i < 40; i++) {
        px.addAll([255, 255, 255, 255]); // white background
      }
      for (var i = 0; i < 20; i++) {
        px.addAll([128, 128, 128, 255]); // grey text
      }
      for (var i = 0; i < 15; i++) {
        px.addAll([230, 30, 40, 255]); // red mark
      }
      final c = StationColors.dominantFromRgba(Uint8List.fromList(px))!;
      final hsl = HSLColor.fromColor(c);
      expect(hsl.hue < 10 || hsl.hue > 350, isTrue, reason: 'red wins, got $hsl');
      expect(hsl.lightness, inInclusiveRange(0.30, 0.52), reason: 'dark enough for white text');
    });

    test('grey-only logos fall back to a stable name hue', () {
      final grey = Uint8List.fromList([for (var i = 0; i < 50; i++) ...[100, 100, 100, 255]]);
      expect(StationColors.dominantFromRgba(grey), isNull);
      expect(StationColors.fallbackFor('Triple J'), StationColors.fallbackFor('Triple J'));
      expect(StationColors.fallbackFor('Triple J'), isNot(StationColors.fallbackFor('ABC Jazz')));
    });

    testWidgets('live equaliser stays still with reduced motion', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: MediaQuery(data: MediaQueryData(disableAnimations: true), child: LiveEqualizer()),
        ),
      );
      await tester.pumpAndSettle(); // would time out if it kept animating
      expect(find.byType(LiveEqualizer), findsOneWidget);
    });
  });

  group('Spotify mark follows the brand rules', () {
    test('monochrome on our tinted surfaces, green only on pure black/white', () {
      expect(SpotifyMark.assetFor(full: false, brightness: Brightness.light), 'assets/brand/spotify/icon_black.png');
      expect(SpotifyMark.assetFor(full: false, brightness: Brightness.dark), 'assets/brand/spotify/icon_white.png');
      expect(SpotifyMark.assetFor(full: true, brightness: Brightness.dark, green: true), 'assets/brand/spotify/logo_green.png');
    });

    testWidgets('never drawn below the minimum size', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: Center(child: SpotifyMark.icon(size: 12))));
      expect(tester.getSize(find.byType(Image)).width, SpotifyMark.minIcon);
      await tester.pumpWidget(const MaterialApp(home: Center(child: SpotifyMark.logo(size: 10))));
      expect(tester.getSize(find.byType(Image)).width, closeTo(SpotifyMark.minLogoWidth, 0.01));
    });
  });

  testWidgets('reconnecting banner', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ReconnectingBanner(visible: true))));
    expect(find.text('Reconnecting…'), findsOneWidget);
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ReconnectingBanner(visible: false))));
    await tester.pumpAndSettle();
    expect(find.text('Reconnecting…'), findsNothing);
  });
}
