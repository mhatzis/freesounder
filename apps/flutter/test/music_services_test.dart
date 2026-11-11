import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/music_service_form.dart';
import 'package:freesounder/ui/music_services_screen.dart';
import 'package:freesounder/ui/settings_screen.dart';
import 'package:freesounder/models/music_service.dart';

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

void main() {
  testWidgets('settings lists Music services', (tester) async {
    tester.view.physicalSize = const Size(400, 1000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host(const SettingsScreen()));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('music-services-tile')), findsOneWidget);
  });

  testWidgets('demo list shows Spotify connected and Tidal available', (tester) async {
    tester.view.physicalSize = const Size(400, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host(const MusicServicesScreen()));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('connected-spotify')), findsOneWidget);
    expect(find.text('Spotify'), findsWidgets);
    expect(find.textContaining('demo@example.com'), findsOneWidget);
    expect(find.byKey(const Key('add-tidal')), findsOneWidget);
    expect(find.byKey(const Key('add-service-grid')), findsOneWidget);
    expect(find.text('Tidal'), findsWidgets);
    expect(find.text('Spotify Connect', skipOffstage: false), findsOneWidget);
    expect(find.byKey(const Key('spotify-connect-kitchen'), skipOffstage: false), findsOneWidget);
  });

  testWidgets('adding Tidal in demo connects it, and it can be removed', (tester) async {
    tester.view.physicalSize = const Size(400, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host(const MusicServicesScreen()));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.byKey(const Key('add-tidal')));
    await tester.tap(find.byKey(const Key('add-tidal')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('connected-tidal')), findsOneWidget);

    await tester.tap(find.byKey(const Key('connected-tidal')));
    await tester.pumpAndSettle();
    expect(find.text('Tidal'), findsWidgets);
    expect(find.byKey(const Key('remove-service')), findsOneWidget);

    await tester.tap(find.byKey(const Key('remove-service')));
    await tester.pumpAndSettle();
    expect(find.text('Remove Tidal?'), findsOneWidget);
    await tester.tap(find.byKey(const Key('confirm-remove-service')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('connected-tidal')), findsNothing);
    expect(find.byKey(const Key('add-tidal')), findsOneWidget);
  });

  testWidgets('Apple Music warns that it is limited', (tester) async {
    tester.view.physicalSize = const Size(400, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_host(const MusicServicesScreen()));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.byKey(const Key('add-apple_music')));
    await tester.tap(find.byKey(const Key('add-apple_music')));
    await tester.pumpAndSettle();
    expect(find.textContaining('limited and experimental'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('connected-apple_music')), findsNothing);
  });

  testWidgets('dynamic form renders string, secret, boolean and dropdown', (tester) async {
    var submitted = <String, dynamic>{};
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: MusicServiceForm(
            fields: const [
              ConfigField(key: 'username', type: 'string', label: 'Username', requiredField: true),
              ConfigField(key: 'password', type: 'secure_string', label: 'Password', secret: true, requiredField: true),
              ConfigField(key: 'lossy', type: 'boolean', label: 'Allow lossy'),
              ConfigField(
                key: 'quality',
                type: 'dropdown',
                label: 'Quality',
                options: [
                  FormOption(value: 'cd', title: 'CD'),
                  FormOption(value: 'hires', title: 'Hi-res'),
                ],
              ),
            ],
            onSubmit: (values) async {
              submitted = values;
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('field-username')), 'ada');
    await tester.enterText(find.byKey(const Key('field-password')), 'secret');
    await tester.tap(find.byKey(const Key('music-service-submit')));
    await tester.pumpAndSettle();
    expect(submitted['username'], 'ada');
    expect(submitted['password'], 'secret');
  });
}
