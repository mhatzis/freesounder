import 'dart:async';
import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/control_client.dart';
import 'package:freesounder/models/now_playing.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A player that answers slowly and reports the volume it last applied.
class SlowPlayer {
  var volume = 20;
  var state = 'playing';
  final volumePosts = <int>[];
  final streams = <StreamController<Map<String, dynamic>>>[];

  Map<String, dynamic> room() => {
        'id': 'patio',
        'name': 'Patio',
        'member_ids': ['patio'],
        'volume': volume,
        'source': 'radio',
        'now_playing': {'title': 'Song'},
      };

  MockClient get client => MockClient((request) async {
        switch (request.url.path) {
          case '/api/v2/rooms':
            return http.Response(jsonEncode([room()]), 200);
          case '/api/v2/now-playing':
            return http.Response(
              jsonEncode({'player_id': 'patio', 'source': 'radio', 'state': state, 'title': 'Song'}),
              200,
            );
          case '/api/v2/rooms/patio/volume':
            await Future<void>.delayed(const Duration(milliseconds: 30));
            final v = (jsonDecode(request.body) as Map)['volume'] as int;
            volumePosts.add(v);
            volume = v;
            return http.Response('{"ok":true}', 200);
          case '/api/v2/groups/patio/pause':
            await Future<void>.delayed(const Duration(milliseconds: 200));
            state = 'paused';
            return http.Response('{"ok":true}', 200);
          case '/health':
            return http.Response(jsonEncode({'status': 'ok', 'version': '0.6.7'}), 200);
        }
        return http.Response('{"detail":"Not Found"}', 404);
      });

  void pushRooms() => streams.last.add({'type': 'event', 'event': 'rooms', 'data': [room()]});
  void pushNp(String s) => streams.last.add({
        'type': 'event',
        'event': 'now_playing',
        'data': {'player_id': 'patio', 'source': 'radio', 'state': s, 'title': 'Song'},
      });
}

class SlowClient extends ControlApiClient {
  SlowClient(this.player, String url) : super(baseUrl: url, httpClient: player.client);
  final SlowPlayer player;

  @override
  Stream<Map<String, dynamic>> connectEvents() {
    final c = StreamController<Map<String, dynamic>>();
    player.streams.add(c);
    return c.stream;
  }
}

void main() {
  late SlowPlayer player;
  late ProviderContainer container;
  late PlaybackStore store;
  PlaybackState s() => container.read(playbackProvider);
  int? vol() => s().rooms.firstWhere((r) => r.id == 'patio').volume;

  setUp(() async {
    player = SlowPlayer();
    container = ProviderContainer(
      overrides: [
        appConfigProvider.overrideWithValue(const AppConfig(discoveryTimeout: Duration.zero)),
        controlClientFactoryProvider.overrideWithValue((url) => SlowClient(player, url)),
      ],
    );
    addTearDown(container.dispose);
    container.read(playbackProvider);
    store = container.read(playbackProvider.notifier);
    expect(await store.connectTo('http://192.168.1.10:8080', persist: false), isTrue);
    store.selectRoom('patio');
    await Future<void>.delayed(const Duration(milliseconds: 50));
  });

  test('dragging updates at once, sends ~10/s, and lands on the final value', () async {
    for (var v = 21; v <= 60; v++) {
      store.dragVolume('patio', v.toDouble());
      expect(vol(), v, reason: 'slider shows the value at once');
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
    // An old rooms event (volume 20 or so) arrives mid-drag: no snap back.
    player.volume = 25;
    player.pushRooms();
    await Future<void>.delayed(const Duration(milliseconds: 5));
    expect(vol(), 60);
    await store.commitVolume('patio', 60);
    expect(player.volumePosts.last, 60);
    expect(player.volumePosts.length, lessThan(15), reason: 'throttled, not one post per step');
    expect(player.volumePosts.length, greaterThan(2), reason: 'live updates while dragging');
    // A stale poll just after release does not snap back either.
    player.volume = 41;
    player.pushRooms();
    await Future<void>.delayed(const Duration(milliseconds: 5));
    expect(vol(), 60);
    // Later, the player's value is shown again.
    await Future<void>.delayed(PlaybackStore.holdAfterChange + const Duration(milliseconds: 100));
    player.pushRooms();
    await Future<void>.delayed(const Duration(milliseconds: 5));
    expect(vol(), 41);
  });

  test('pause shows at once and a stale "playing" event does not flip it back', () async {
    expect(s().nowPlaying?.isPlaying, isTrue);
    final pending = store.togglePlayPause();
    await Future<void>.delayed(const Duration(milliseconds: 5));
    expect(s().nowPlaying?.state, 'paused', reason: 'before the player answered');
    player.pushNp('playing'); // sent before the pause reached the player
    await Future<void>.delayed(const Duration(milliseconds: 5));
    expect(s().nowPlaying?.state, 'paused');
    await pending;
    expect(player.state, 'paused');
  });

  test('NowPlaying copy keeps metadata fields', () {
    final np = NowPlaying.fromJson({'player_id': 'p', 'title_source': 'stream', 'meta_from': 'Patio'});
    final c = np.copyWith(state: 'paused');
    expect((c.titleSource, c.metaFrom), ('stream', 'Patio'));
  });
}
