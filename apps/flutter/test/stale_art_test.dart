import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/control_client.dart';
import 'package:freesounder/models/now_playing.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/artwork.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A player playing radio in two rooms; the song can change behind the app's back.
class SongPlayer {
  final streams = <StreamController<Map<String, dynamic>>>[];
  final songs = <String, Map<String, dynamic>>{
    'patio': {'title': 'Song A', 'artist': 'Air', 'image_url': 'https://art/a.jpg'},
    'kitchen': {'title': 'Song K', 'artist': 'Moby', 'image_url': 'https://art/k.jpg'},
  };
  var npCalls = 0;

  Map<String, dynamic> room(String id) => {
        'id': id,
        'name': id,
        'member_ids': [id],
        'source': 'radio',
        'now_playing': songs[id],
      };

  Map<String, dynamic> np(String id) => {
        'player_id': id,
        'source': 'radio',
        'state': 'playing',
        'station_image_url': 'https://logo/station.png',
        ...songs[id]!,
      };

  MockClient get client => MockClient((request) async {
        switch (request.url.path) {
          case '/api/v2/rooms':
            return http.Response(jsonEncode([room('kitchen'), room('patio')]), 200);
          case '/api/v2/now-playing':
            npCalls++;
            return http.Response(jsonEncode(np(request.url.queryParameters['player_id']!)), 200);
          case '/health':
            return http.Response(jsonEncode({'status': 'ok', 'version': '0.6.6'}), 200);
        }
        return http.Response('{"detail":"Not Found"}', 404);
      });

  void push(String event, Object data) => streams.last.add({'type': 'event', 'event': event, 'data': data});
}

class SongClient extends ControlApiClient {
  SongClient(this.player, String url) : super(baseUrl: url, httpClient: player.client);
  final SongPlayer player;

  @override
  Stream<Map<String, dynamic>> connectEvents() {
    final c = StreamController<Map<String, dynamic>>();
    player.streams.add(c);
    return c.stream;
  }
}

Future<void> waitFor(bool Function() ok) async {
  final end = DateTime.now().add(const Duration(seconds: 3));
  while (!ok()) {
    if (DateTime.now().isAfter(end)) {
      fail('condition not met');
    }
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
}

void main() {
  group('store', () {
    late SongPlayer player;
    late ProviderContainer container;
    late PlaybackStore store;
    PlaybackState s() => container.read(playbackProvider);

    setUp(() async {
      player = SongPlayer();
      container = ProviderContainer(
        overrides: [
          appConfigProvider.overrideWithValue(const AppConfig(discoveryTimeout: Duration.zero)),
          controlClientFactoryProvider.overrideWithValue((url) => SongClient(player, url)),
        ],
      );
      addTearDown(container.dispose);
      container.read(playbackProvider);
      store = container.read(playbackProvider.notifier);
      expect(await store.connectTo('http://192.168.1.10:8080', persist: false), isTrue);
      store.selectRoom('patio');
      await waitFor(() => s().nowPlaying?.title == 'Song A');
    });

    test('a new song in the rooms list with no now_playing event refreshes the cover', () async {
      player.songs['patio'] = {'title': 'Song B', 'artist': 'Air', 'image_url': 'https://art/b.jpg'};
      // Only the rooms event arrives (the now_playing one was lost).
      player.push('rooms', [player.room('kitchen'), player.room('patio')]);
      await waitFor(() => s().nowPlaying?.imageUrl == 'https://art/b.jpg');
      expect(s().nowPlaying!.title, 'Song B');
      expect(s().nowPlaying!.artworkCandidates.first, 'https://art/b.jpg');
    });

    test('switching rooms never shows the previous room\'s song', () async {
      player.push('now_playing', player.np('kitchen'));
      await waitFor(() => store.nowPlayingFor('kitchen')?.title == 'Song K');
      store.selectRoom('kitchen');
      expect(s().nowPlaying?.title, 'Song K', reason: 'known song shown at once');
      store.selectRoom('patio');
      store.selectRoom('kitchen');
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(s().nowPlaying?.playerId, 'kitchen');
    });

    test('resume reloads artwork', () async {
      final before = artworkGeneration.value;
      store.appPaused();
      await store.appResumed();
      expect(artworkGeneration.value, before + 1);
    });
  });

  group('periodic refresh', () {
    test('a silent song change is picked up within the refresh interval', () async {
      final player = SongPlayer();
      final container = ProviderContainer(
        overrides: [
          appConfigProvider.overrideWithValue(const AppConfig(discoveryTimeout: Duration.zero)),
          controlClientFactoryProvider.overrideWithValue((url) => SongClient(player, url)),
          linkTimingsProvider.overrideWithValue(
            const LinkTimings(heartbeat: Duration(seconds: 30), nowPlayingRefresh: Duration(milliseconds: 30)),
          ),
        ],
      );
      addTearDown(container.dispose);
      container.read(playbackProvider);
      final store = container.read(playbackProvider.notifier);
      expect(await store.connectTo('http://192.168.1.10:8080', persist: false), isTrue);
      store.selectRoom('patio');
      await waitFor(() => container.read(playbackProvider).nowPlaying?.title == 'Song A');
      // No event at all (socket silently stale): the timer still catches it.
      player.songs['patio'] = {'title': 'Song C', 'artist': 'Air', 'image_url': 'https://art/c.jpg'};
      await waitFor(() => container.read(playbackProvider).nowPlaying?.title == 'Song C');
      expect(container.read(playbackProvider).nowPlaying!.imageUrl, 'https://art/c.jpg');
      // Not while in the background.
      store.appPaused();
      final calls = player.npCalls;
      await Future<void>.delayed(const Duration(milliseconds: 120));
      expect(player.npCalls, calls);
    });
  });

  test('developer metadata line', () {
    final now = DateTime.utc(2026, 11, 19, 1, 0, 30);
    final np = NowPlaying.fromJson({
      'player_id': 'p',
      'title': 'x',
      'meta_updated_at': DateTime.utc(2026, 11, 19, 1, 0, 0).millisecondsSinceEpoch / 1000,
      'title_checked_at': DateTime.utc(2026, 11, 19, 1, 0, 20).millisecondsSinceEpoch / 1000,
      'meta_from': 'Patio',
    }, receivedAt: DateTime.utc(2026, 11, 19, 1, 0, 25));
    final line = metadataDebugLine(np, now);
    expect(line, contains('title read 10s ago'));
    expect(line, contains('by Patio'));
    expect(line, contains('app got it 5s ago'));
    expect(line, startsWith('song changed '));
  });

  testWidgets('same art URL, new song: the image is rebuilt', (tester) async {
    const a = NowPlaying(playerId: 'p', title: 'One', imageUrl: 'https://art/station.png', source: 'radio');
    const b = NowPlaying(playerId: 'p', title: 'Two', imageUrl: 'https://art/station.png', source: 'radio');
    Widget art(NowPlaying np) => MaterialApp(
          home: SizedBox(width: 40, height: 40, child: Artwork(urls: np.artworkCandidates, trackKey: np.trackKey)),
        );
    await tester.pumpWidget(art(a));
    final first = tester.widget<Image>(find.byType(Image)).key;
    await tester.pumpWidget(art(b));
    final second = tester.widget<Image>(find.byType(Image)).key;
    expect(first, isNot(second));
    expect(a.trackKey, isNot(b.trackKey));
  });
}
