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
  final controls = <String>[];
  var npDelay = Duration.zero;
  var dropNextControl = false;
  var pauseDelay = const Duration(milliseconds: 200);
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
            final seen = state; // what the player reported when asked
            await Future<void>.delayed(npDelay);
            return http.Response(
              jsonEncode({'player_id': 'patio', 'source': 'radio', 'state': seen, 'title': 'Song'}),
              200,
            );
          case '/api/v2/groups/patio/play':
            controls.add('play');
            state = 'playing';
            return http.Response('{"ok":true}', 200);
          case '/api/v2/rooms/patio/volume':
            await Future<void>.delayed(const Duration(milliseconds: 30));
            final v = (jsonDecode(request.body) as Map)['volume'] as int;
            volumePosts.add(v);
            volume = v;
            return http.Response('{"ok":true}', 200);
          case '/api/v2/groups/patio/toggle':
            controls.add('toggle');
            state = state == 'playing' ? 'paused' : 'playing';
            return http.Response('{"ok":true}', 200);
          case '/api/v2/groups/patio/pause':
            controls.add('pause');
            if (dropNextControl) {
              dropNextControl = false;
              throw http.ClientException('Connection closed before full header was received');
            }
            await Future<void>.delayed(pauseDelay);
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

  test('pause right after waking: explicit pause, stale wake-up read does not flip it', () async {
    expect(s().nowPlaying?.isPlaying, isTrue);
    store.appPaused();
    // While the phone sleeps nothing changes; on wake the fetch is slow and
    // its answer ("playing") is from before the pause.
    player.npDelay = const Duration(milliseconds: 300);
    final resumed = store.appResumed();
    expect(player.streams.length, 2, reason: 'event stream reopened at once on resume');
    await Future<void>.delayed(const Duration(milliseconds: 20));
    final pending = store.togglePlayPause();
    await Future<void>.delayed(const Duration(milliseconds: 5));
    expect(s().nowPlaying?.state, 'paused', reason: 'what the user tapped shows at once');
    await resumed;
    expect(s().nowPlaying?.state, 'paused', reason: 'a read that left before the tap cannot undo it');
    await pending;
    expect(player.controls, ['pause'], reason: 'explicit pause, never a toggle');
    expect(player.state, 'paused');
    player.npDelay = Duration.zero;
    await Future<void>.delayed(const Duration(milliseconds: 1600));
    expect(s().nowPlaying?.state, 'paused');
  });

  test('pausing a player that already paused elsewhere keeps it paused', () async {
    // The screen still says playing (stale), the player is already paused.
    player.state = 'paused';
    await store.togglePlayPause();
    expect(player.controls, ['pause'], reason: 'pause on a paused player is a no-op, not play');
    expect(player.state, 'paused');
    expect(s().nowPlaying?.state, 'paused');
  });

  test('a dead socket after wake: the pause is retried once and lands', () async {
    player.dropNextControl = true;
    await store.togglePlayPause();
    expect(player.controls, ['pause', 'pause']);
    expect(player.state, 'paused');
    expect(s().nowPlaying?.state, 'paused');
  });

  test('once the player confirms, a later real change shows without waiting out the hold', () async {
    await store.togglePlayPause();
    await Future<void>.delayed(const Duration(milliseconds: 400)); // read-back confirms "paused"
    expect(s().nowPlaying?.state, 'paused');
    player.state = 'playing'; // someone presses play on another phone
    player.pushNp('playing');
    await Future<void>.delayed(const Duration(milliseconds: 5));
    expect(s().nowPlaying?.state, 'playing');
  });

  test('a pause slower than the hold: the icon stays paused and one press resumes', () async {
    // 0.6.14 with a Bluetooth speaker: the pause took ~9 s to land, the hold
    // ran out first, a "playing" report won, and resuming took two presses.
    final hold = PlaybackStore.playHoldMax;
    final after = PlaybackStore.playHoldAfterReply;
    PlaybackStore.playHoldMax = const Duration(milliseconds: 300);
    PlaybackStore.playHoldAfterReply = const Duration(milliseconds: 300);
    addTearDown(() {
      PlaybackStore.playHoldMax = hold;
      PlaybackStore.playHoldAfterReply = after;
    });
    player.pauseDelay = const Duration(milliseconds: 700);
    final pending = store.togglePlayPause();
    await Future<void>.delayed(const Duration(milliseconds: 450)); // past the hold, still sending
    player.pushNp('playing'); // the player has not paused yet
    await Future<void>.delayed(const Duration(milliseconds: 5));
    expect(s().nowPlaying?.state, 'paused', reason: 'no flip back while the pause is on its way');
    await pending;
    expect(player.state, 'paused');
    player.pushNp('playing'); // an event that left just before the pause landed
    await Future<void>.delayed(const Duration(milliseconds: 5));
    expect(s().nowPlaying?.state, 'paused');
    await Future<void>.delayed(const Duration(milliseconds: 400));
    expect(s().nowPlaying?.state, 'paused', reason: 'the icon matches the player');
    // One press resumes.
    await store.togglePlayPause();
    expect(player.controls, ['pause', 'play']);
    expect(player.state, 'playing');
    expect(s().nowPlaying?.state, 'playing');
  });

  test('NowPlaying copy keeps metadata fields', () {
    final np = NowPlaying.fromJson({'player_id': 'p', 'title_source': 'stream', 'meta_from': 'Patio'});
    final c = np.copyWith(state: 'paused');
    expect((c.titleSource, c.metaFrom), ('stream', 'Patio'));
  });
}
