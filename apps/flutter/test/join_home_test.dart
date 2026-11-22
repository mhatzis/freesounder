import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/state/join_home.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/join_home_screen.dart';

void main() {
  testWidgets('join screen names the home and asks for its code', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          appConfigProvider.overrideWithValue(const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero)),
        ],
        child: const MaterialApp(
          home: JoinHomeScreen(prompt: JoinPrompt(householdId: 'hh1', name: 'Hatzis')),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Join the Hatzis home'), findsOneWidget);
    expect(find.byKey(const Key('join-home-code')), findsOneWidget);
    await tester.enterText(find.byKey(const Key('join-home-code')), '12a3 456');
    expect(find.text('123 456'), findsOneWidget); // digits and spaces only
    // Demo mode has no player: joining says so instead of failing silently.
    await tester.tap(find.byKey(const Key('join-home-button')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('join-home-error')), findsOneWidget);
  });
}
