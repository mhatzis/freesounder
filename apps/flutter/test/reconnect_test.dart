import 'dart:async';
import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/control_client.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A player whose event stream can be cut and whose HTTP can go away.
class FlakyPlayer {
  var reachable = true;
  var roomsCalls = 0;
  var healthCalls = 0;
  final streams = <StreamController<Map<String, dynamic>>>[];

  MockClient get client => MockClient((request) async {
        if (request.url.path == '/health') {
          healthCalls++;
        }
        if (!reachable) {
          throw http.ClientException('Network is unreachable');
        }
        switch (request.url.path) {
          case '/api/v2/rooms':
            roomsCalls++;
            return http.Response('[]', 200);
          case '/health':
            return http.Response(jsonEncode({'status': 'ok', 'version': '0.6.2'}), 200);
        }
        return http.Response('{"detail":"Not Found"}', 404);
      });
}

class FlakyClient extends ControlApiClient {
  FlakyClient(this.player, String url) : super(baseUrl: url, httpClient: player.client);

  final FlakyPlayer player;

  @override
  Stream<Map<String, dynamic>> connectEvents() {
    final c = StreamController<Map<String, dynamic>>();
    player.streams.add(c);
    return c.stream;
  }
}

Future<void> spin([int n = 10]) async {
  for (var i = 0; i < n; i++) {
    await Future<void>.delayed(Duration.zero);
  }
}

Future<void> waitFor(bool Function() ok, {Duration timeout = const Duration(seconds: 3)}) async {
  final end = DateTime.now().add(timeout);
  while (!ok()) {
    if (DateTime.now().isAfter(end)) {
      fail('condition not met in $timeout');
    }
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
}

void main() {
  late FlakyPlayer player;
  late ProviderContainer container;
  late PlaybackStore store;

  setUp(() async {
    player = FlakyPlayer();
    container = ProviderContainer(
      overrides: [
        appConfigProvider.overrideWithValue(const AppConfig(discoveryTimeout: Duration.zero)),
        controlClientFactoryProvider.overrideWithValue((url) => FlakyClient(player, url)),
        linkTimingsProvider.overrideWithValue(
          const LinkTimings(
            heartbeat: Duration(milliseconds: 40),
            firstRetry: Duration(milliseconds: 10),
            maxRetry: Duration(milliseconds: 40),
          ),
        ),
      ],
    );
    addTearDown(container.dispose);
    container.read(playbackProvider);
    await spin();
    store = container.read(playbackProvider.notifier);
    expect(await store.connectTo('http://192.168.1.10:8080', persist: false), isTrue);
  });

  test('a dropped event stream shows Reconnecting and recovers on its own', () async {
    final roomsBefore = player.roomsCalls;
    await player.streams.last.close(); // the socket died (Wi-Fi blip, server restart)
    await spin();
    expect(container.read(playbackProvider).reconnecting, isTrue);
    await waitFor(() => !container.read(playbackProvider).reconnecting);
    expect(player.roomsCalls, greaterThan(roomsBefore), reason: 'rooms are re-fetched');
    expect(player.streams.length, 2, reason: 'a fresh event stream is opened');
    expect(container.read(playbackProvider).connected, isTrue);
  });

  test('heartbeat notices a silent outage and backs off until the player is back', () async {
    player.reachable = false;
    await waitFor(() => container.read(playbackProvider).reconnecting);
    await Future<void>.delayed(const Duration(milliseconds: 120));
    expect(container.read(playbackProvider).reconnecting, isTrue);
    player.reachable = true;
    await waitFor(() => !container.read(playbackProvider).reconnecting);
    expect(player.healthCalls, greaterThan(1));
  });

  test('resume re-fetches everything and reopens the stream, even if it looked alive', () async {
    store.appPaused();
    final roomsBefore = player.roomsCalls;
    final streamsBefore = player.streams.length;
    await store.appResumed();
    expect(player.roomsCalls, roomsBefore + 1);
    expect(player.streams.length, streamsBefore + 1);
    expect(container.read(playbackProvider).reconnecting, isFalse);
  });

  test('resume while the player is unreachable keeps retrying', () async {
    store.appPaused();
    player.reachable = false;
    await store.appResumed();
    expect(container.read(playbackProvider).reconnecting, isTrue);
    player.reachable = true;
    await waitFor(() => !container.read(playbackProvider).reconnecting);
  });
}
