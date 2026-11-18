import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/connection_prefs.dart';
import 'package:freesounder/api/first_player.dart';
import 'package:freesounder/models/player_settings.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'first_player_test.dart' show FakeControlClient, FakeLan, fastSetup;

const oldHost = 'http://192.168.1.27:8080';

Future<(ProviderContainer, ConnectionPrefs)> start(
  FakeLan lan, {
  List<DiscoveredHost> mdns = const [],
  Map<String, Object> saved = const {'host_url': oldHost, 'household_id': 'home-old'},
}) async {
  SharedPreferences.setMockInitialValues(saved);
  final prefs = ConnectionPrefs(await SharedPreferences.getInstance());
  final container = ProviderContainer(
    overrides: [
      appConfigProvider.overrideWithValue(const AppConfig(discoveryTimeout: Duration(milliseconds: 1))),
      connectionPrefsProvider.overrideWithValue(prefs),
      discoveryProvider.overrideWithValue((_) async => mdns),
      controlClientFactoryProvider.overrideWithValue((url) => FakeControlClient(url, lan.client)),
      firstPlayerSetupProvider.overrideWithValue(() => fastSetup(lan.client)),
    ],
  );
  addTearDown(container.dispose);
  container.read(playbackProvider);
  for (var i = 0; i < 40; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 2));
  }
  container.read(playbackProvider.notifier).probeWait = Duration.zero;
  return (container, prefs);
}

Future<AdoptFailure?> addPatio(ProviderContainer c, {bool allowNewHome = false}) {
  return c
      .read(playbackProvider.notifier)
      .adoptProvisioned(name: 'Patio', ip: '192.168.1.24', allowNewHome: allowNewHome)
      .then<AdoptFailure?>((_) => null, onError: (Object e) => e as AdoptFailure);
}

void main() {
  test('both Pis reflashed: the old home is gone, so offer a new home instead of a dead end', () async {
    final lan = FakeLan()..down.add('192.168.1.27');
    final (c, prefs) = await start(lan);
    expect(c.read(playbackProvider).connected, isFalse);

    final failure = await addPatio(c);
    expect(failure!.recovery, AdoptRecovery.forgetOldHome);
    expect(failure.message, contains("previous home isn't on this network"));
    expect(lan.to('/api/v2/setup'), isEmpty, reason: 'nothing is set up without the user choosing it');

    // "Set up a new home": the old home is forgotten and Patio becomes the first player.
    expect(await addPatio(c, allowNewHome: true), isNull);
    expect(lan.to('/api/v2/setup').single.url.host, '192.168.1.24');
    expect(prefs.savedHost, 'http://192.168.1.24:8080');
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(prefs.householdId, 'home-1', reason: 'the new household replaces the old one');
    expect(c.read(playbackProvider).connected, isTrue);
  });

  test('the home is alive at a new IP: found again over mDNS and the player joins it', () async {
    final lan = FakeLan()
      ..down.add('192.168.1.27')
      ..ready.add('192.168.1.30')
      ..households['192.168.1.30'] = 'home-old';
    final (c, prefs) = await start(lan, mdns: const [
      DiscoveredHost(url: 'http://192.168.1.30:8080', name: 'Lounge', state: 'ready', household: 'home-old'),
    ]);
    // Startup already found it at its new address.
    expect(c.read(playbackProvider).hostUrl, 'http://192.168.1.30:8080');
    expect(prefs.savedHost, 'http://192.168.1.30:8080');
    expect(await addPatio(c), isNull);
    expect(lan.to('/api/v2/players/adopt').single.url.host, '192.168.1.30');
    expect(lan.to('/api/v2/setup'), isEmpty, reason: 'joined the existing home, no split');
  });

  test('home seen on the network but not answering: never offer to forget it', () async {
    final lan = FakeLan()..down.addAll(['192.168.1.27', '192.168.1.30']);
    final (c, _) = await start(lan, mdns: const [
      DiscoveredHost(url: 'http://192.168.1.30:8080', name: 'Lounge', state: 'ready', household: 'home-old'),
      DiscoveredHost(url: 'http://192.168.1.40:8080', name: 'Neighbour', state: 'ready', household: 'home-x'),
    ]);
    expect(c.read(playbackProvider).connected, isFalse, reason: 'does not jump to another home');
    final failure = await addPatio(c);
    expect(failure!.recovery, AdoptRecovery.startNewHome);
    expect(lan.to('/api/v2/setup'), isEmpty);
  });

  test('old home gone, another set-up home on the network: connect to it and remember it', () async {
    final lan = FakeLan()
      ..down.add('192.168.1.27')
      ..ready.add('192.168.1.24')
      ..households['192.168.1.24'] = 'home-new';
    final (c, prefs) = await start(lan, mdns: const [
      DiscoveredHost(url: 'http://192.168.1.24:8080', name: 'Patio', state: 'ready', household: 'home-new'),
    ]);
    expect(c.read(playbackProvider).hostUrl, 'http://192.168.1.24:8080');
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(prefs.householdId, 'home-new');
    expect(prefs.savedHost, 'http://192.168.1.24:8080');
  });
}
