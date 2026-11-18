import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/control_client.dart';
import 'package:freesounder/api/errors.dart';
import 'package:freesounder/api/first_player.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/rooms_panel.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A fake LAN: boards answering the v2 player API on :8080.
class FakeLan {
  final requests = <http.Request>[];
  var infoFailuresLeft = 0;
  int? setupStatus;
  String setupBody = '{"detail":"boom"}';
  int? controlAdoptStatus;
  final ready = <String>{};

  /// Hosts that drop every request (powered off / unreachable).
  final down = <String>{};

  /// Household id per ready host (default 'home-1').
  final households = <String, String>{};

  /// Connected players that answer adopt with 502 (they can't reach the board).
  final adoptUnreachableFrom = <String>{};

  /// What /api/v2/rooms returns.
  List<Map<String, dynamic>> rooms = const [];

  MockClient get client => MockClient((request) async {
        requests.add(request);
        final path = request.url.path;
        final host = request.url.host;
        if (down.contains(host)) {
          throw const SocketException('No route to host');
        }
        if (request.url.port != 8080) {
          return http.Response('{"detail":"Not Found"}', 404);
        }
        if (path == '/api/v2/info') {
          if (infoFailuresLeft > 0) {
            infoFailuresLeft--;
            throw const SocketException('Local network prohibited');
          }
          return http.Response(
            jsonEncode({
              'id': 'pi-$host',
              'state': ready.contains(host) ? 'ready' : 'unclaimed',
              'household': ready.contains(host) ? {'id': households[host] ?? 'home-1', 'name': 'Home'} : null,
              'version': '0.6.0',
            }),
            200,
          );
        }
        if (path == '/api/v2/setup') {
          if (setupStatus != null) {
            return http.Response(setupBody, setupStatus!);
          }
          ready.add(host);
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          return http.Response(jsonEncode({'id': 'pi-$host', 'room': body['room'], 'state': 'ready'}), 200);
        }
        if (path == '/health') {
          return http.Response(jsonEncode({'status': 'ok', 'version': '0.6.0'}), 200);
        }
        if (path == '/api/v2/rooms') {
          return http.Response(jsonEncode(rooms), 200);
        }
        if (path == '/api/v2/now-playing') {
          return http.Response(jsonEncode({'player_id': request.url.queryParameters['player_id'], 'state': 'idle'}), 200);
        }
        if (path == '/api/v2/reset') {
          ready.remove(host);
          households.remove(host);
          return http.Response('{"ok":true}', 200);
        }
        if (path == '/api/v2/players/adopt') {
          if (adoptUnreachableFrom.contains(host)) {
            return http.Response('{"detail":"New player did not answer this player"}', 502);
          }
          final target = (jsonDecode(request.body) as Map<String, dynamic>)['ip'] as String?;
          if (target != null && ready.contains(target) && households[target] != (households[host] ?? 'home-1')) {
            return http.Response('{"detail":"This player is already set up."}', 409);
          }
          if (controlAdoptStatus != null) {
            return http.Response('{"detail":"New player did not answer: timed out"}', controlAdoptStatus!);
          }
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          return http.Response(jsonEncode({'id': 'pi-${body['ip']}', 'room': body['room'], 'state': 'ready'}), 200);
        }
        return http.Response('{"detail":"Not Found"}', 404);
      });

  List<http.Request> to(String path) => requests.where((r) => r.url.path == path).toList();
}

class FakeControlClient extends ControlApiClient {
  FakeControlClient(String url, http.Client client) : super(baseUrl: url, httpClient: client);

  @override
  Stream<Map<String, dynamic>> connectEvents() => const Stream.empty();
}

FirstPlayerSetup fastSetup(http.Client client) => FirstPlayerSetup(
      httpClient: client,
      reachTimeout: const Duration(milliseconds: 300),
      retryDelay: const Duration(milliseconds: 10),
    );

ProviderContainer containerFor(FakeLan lan) {
  final container = ProviderContainer(
    overrides: [
      appConfigProvider.overrideWithValue(const AppConfig(discoveryTimeout: Duration.zero)),
      controlClientFactoryProvider.overrideWithValue((url) => FakeControlClient(url, lan.client)),
      firstPlayerSetupProvider.overrideWithValue(() => fastSetup(lan.client)),
    ],
  );
  addTearDown(container.dispose);
  return container;
}

Future<void> settle(ProviderContainer container) async {
  container.read(playbackProvider);
  for (var i = 0; i < 5; i++) {
    await Future<void>.delayed(Duration.zero);
  }
}

void main() {
  group('FirstPlayerSetup.createSystem', () {
    test('names the room on the board by IP and waits for it', () async {
      final lan = FakeLan()..infoFailuresLeft = 2; // e.g. the local-network prompt
      final progress = <String>[];
      final base = await fastSetup(lan.client).createSystem(ip: '192.168.1.24', name: 'Lounge', onProgress: progress.add);
      expect(base, 'http://192.168.1.24:8080');
      final setup = lan.to('/api/v2/setup').single;
      expect(setup.url.toString(), 'http://192.168.1.24:8080/api/v2/setup');
      expect(jsonDecode(setup.body), {'room': 'Lounge'});
      expect(lan.to('/api/v2/info').length, 3);
      expect(lan.to('/health'), isNotEmpty);
      expect(progress.first, contains('192.168.1.24'));
    });

    test('an already set-up board is not set up twice', () async {
      final lan = FakeLan()..ready.add('192.168.1.24');
      await fastSetup(lan.client).createSystem(ip: '192.168.1.24', name: 'Lounge');
      expect(lan.to('/api/v2/setup'), isEmpty);
    });

    test('names the step and HTTP status when the player refuses', () async {
      final lan = FakeLan()..setupStatus = 500;
      final failure = await fastSetup(lan.client)
          .createSystem(ip: '192.168.1.24', name: 'Lounge')
          .then<AdoptFailure?>((_) => null, onError: (Object e) => e as AdoptFailure);
      expect(failure!.step, 'Setting up the room');
      expect(failure.statusCode, 500);
      expect(failure.reason, 'boom');
      expect(failure.message, contains('Setting up the room failed (HTTP 500)'));
      expect(failure.debugLine, contains('http://192.168.1.24:8080/api/v2/setup -> HTTP 500: boom'));
    });

    test('reports the network exception when the player never answers', () async {
      final lan = FakeLan()..infoFailuresLeft = 1000;
      final failure = await fastSetup(lan.client)
          .createSystem(ip: '192.168.1.24', name: 'Lounge')
          .then<AdoptFailure?>((_) => null, onError: (Object e) => e as AdoptFailure);
      expect(failure!.step, 'Reaching the player');
      expect(failure.statusCode, isNull);
      expect(failure.reason, contains('Local network prohibited'));
      expect(failure.message, contains('Local Network'));
    });
  });

  group('PlaybackStore.adoptProvisioned', () {
    test('first player: nothing connected, so the board is set up and connected', () async {
      final lan = FakeLan();
      final container = containerFor(lan);
      await settle(container);
      expect(container.read(playbackProvider).connected, isFalse);

      await container.read(playbackProvider.notifier).adoptProvisioned(
            name: 'Lounge',
            id: 'pi-1',
            ip: '192.168.1.24',
            url: 'http://192.168.1.24:8080',
          );
      final state = container.read(playbackProvider);
      expect(state.connected, isTrue);
      expect(state.hostUrl, 'http://192.168.1.24:8080');
      expect(lan.to('/api/v2/setup').single.url.host, '192.168.1.24');
      expect(lan.to('/api/v2/players/adopt'), isEmpty);
    });

    test('another player: the connected player hands over the household', () async {
      final lan = FakeLan();
      final container = containerFor(lan);
      await settle(container);
      final store = container.read(playbackProvider.notifier);
      expect(await store.connectTo('http://192.168.1.10:8080', persist: false), isTrue);

      await store.adoptProvisioned(name: 'Kitchen', id: 'pi-2', ip: '192.168.1.77', url: 'http://192.168.1.77:8090');
      final adopt = lan.to('/api/v2/players/adopt').single;
      expect(adopt.url.host, '192.168.1.10');
      expect(jsonDecode(adopt.body), {'room': 'Kitchen', 'ip': '192.168.1.77', 'id': 'pi-192.168.1.77'},
          reason: 'the real id from the board itself, so the player can also find it over mDNS');
      expect(lan.to('/api/v2/info').map((r) => r.url.host), contains('192.168.1.77'),
          reason: 'the phone checks the new board is up before asking');
      expect(lan.to('/api/v2/setup'), isEmpty, reason: 'the connected player sets the new one up');
    });

    test('connected to the very board being added: set it up directly', () async {
      final lan = FakeLan();
      final container = containerFor(lan);
      await settle(container);
      final store = container.read(playbackProvider.notifier);
      await store.connectTo('http://192.168.1.24:8080', persist: false);
      await store.adoptProvisioned(name: 'Lounge', id: 'pi-1', ip: '192.168.1.24');
      expect(lan.to('/api/v2/setup').single.url.host, '192.168.1.24');
      expect(lan.to('/api/v2/players/adopt'), isEmpty);
    });

    test('not connected but a home exists: reconnect, never quietly start a second home', () async {
      final lan = FakeLan()..down.add('192.168.1.10');
      final container = containerFor(lan);
      await settle(container);
      final store = container.read(playbackProvider.notifier);
      expect(await store.connectTo('http://192.168.1.10:8080', persist: false), isFalse);
      final failure = await store
          .adoptProvisioned(name: 'Kitchen', id: 'pi-2', ip: '192.168.1.77')
          .then<AdoptFailure?>((_) => null, onError: (Object e) => e as AdoptFailure);
      expect(failure!.recovery, AdoptRecovery.startNewHome);
      expect(failure.message, contains('separate home'));
      expect(lan.to('/api/v2/setup'), isEmpty, reason: 'the new board was not made its own home');

      // Back on the right Wi-Fi: the same call reconnects and joins the existing home.
      lan.down.clear();
      lan.ready.add('192.168.1.10');
      await store.adoptProvisioned(name: 'Kitchen', id: 'pi-2', ip: '192.168.1.77');
      expect(lan.to('/api/v2/players/adopt').single.url.host, '192.168.1.10');
      expect(lan.to('/api/v2/setup'), isEmpty);
    });

    test('the user can still deliberately start a separate home', () async {
      final lan = FakeLan()..down.add('192.168.1.10');
      final container = containerFor(lan);
      await settle(container);
      final store = container.read(playbackProvider.notifier);
      await store.connectTo('http://192.168.1.10:8080', persist: false);
      await store.adoptProvisioned(name: 'Kitchen', id: 'pi-2', ip: '192.168.1.77', allowNewHome: true);
      expect(lan.to('/api/v2/setup').single.url.host, '192.168.1.77');
    });

    test('if the connected player cannot reach the new board, another player adopts it', () async {
      final lan = FakeLan()
        ..ready.addAll(['192.168.1.10', '192.168.1.11'])
        ..adoptUnreachableFrom.add('192.168.1.10')
        ..rooms = [
          {'id': 'pi-192.168.1.10', 'name': 'Lounge', 'url': 'http://192.168.1.10:8080'},
          {'id': 'pi-192.168.1.11', 'name': 'Kitchen', 'url': 'http://192.168.1.11:8080'},
        ];
      final container = containerFor(lan);
      await settle(container);
      final store = container.read(playbackProvider.notifier)..probeWait = Duration.zero;
      expect(await store.connectTo('http://192.168.1.10:8080', persist: false), isTrue);
      await store.adoptProvisioned(name: 'Office', id: 'pi-3', ip: '192.168.1.77');
      expect(lan.to('/api/v2/players/adopt').map((r) => r.url.host), ['192.168.1.10', '192.168.1.11']);
    });

    test('a board that belongs to another home is offered a reset, then joins', () async {
      final lan = FakeLan()
        ..ready.addAll(['192.168.1.10', '192.168.1.77'])
        ..households['192.168.1.77'] = 'home-other';
      final container = containerFor(lan);
      await settle(container);
      final store = container.read(playbackProvider.notifier)..probeWait = Duration.zero;
      expect(await store.connectTo('http://192.168.1.10:8080', persist: false), isTrue);
      await Future<void>.delayed(const Duration(milliseconds: 20)); // household id loads
      expect(store.householdId, 'home-1');
      final failure = await store
          .adoptProvisioned(name: 'Office', ip: '192.168.1.77')
          .then<AdoptFailure?>((_) => null, onError: (Object e) => e as AdoptFailure);
      expect(failure!.recovery, AdoptRecovery.moveHere);
      expect(lan.to('/api/v2/players/adopt'), isEmpty);

      await store.adoptProvisioned(name: 'Office', ip: '192.168.1.77', resetFirst: true);
      expect(lan.to('/api/v2/reset').single.url.host, '192.168.1.77');
      expect(lan.to('/api/v2/players/adopt').single.url.host, '192.168.1.10');
    });

    test('join failure names the step and status', () async {
      final lan = FakeLan()..controlAdoptStatus = 502;
      final container = containerFor(lan);
      await settle(container);
      final store = container.read(playbackProvider.notifier);
      await store.connectTo('http://192.168.1.10:8080', persist: false);
      final failure = await store
          .adoptProvisioned(name: 'Kitchen', id: 'pi-2', ip: '192.168.1.77')
          .then<AdoptFailure?>((_) => null, onError: (Object e) => e as AdoptFailure);
      expect(failure!.step, 'Adding it to your home');
      expect(failure.statusCode, 502);
      expect(failure.reason, contains('timed out'));
    });
  });

  test('provisionedIp prefers the Improv IP, then the redirect host', () {
    expect(provisionedIp(ip: '192.168.1.24'), '192.168.1.24');
    expect(provisionedIp(redirect: 'http://192.168.1.30:8090'), '192.168.1.30');
    expect(provisionedIp(), isNull);
  });

  test('503 from a starting system becomes a friendly message', () {
    final err = ControlApiException('HTTP 503', statusCode: 503, maState: 'starting', maDetail: 'Unpacking Music Assistant');
    expect(userMessageFor(err), 'Your system is still starting… Unpacking Music Assistant');
    final plain = ControlApiException('HTTP 503', statusCode: 503);
    expect(userMessageFor(plain), startsWith('Your system is still starting…'));
  });

  test('requests never hang: TimeoutClient gives up', () async {
    final never = MockClient((_) => Completer<http.Response>().future);
    final client = ControlApiClient(baseUrl: 'http://192.168.1.24:8080', httpClient: never, requestTimeout: const Duration(milliseconds: 50));
    await expectLater(client.listRooms(), throwsA(isA<TimeoutException>()));
  });

  test('empty rooms: still starting with the reason, not "Set up your first player"', () {
    const starting = PlaybackState(rooms: [], mockMode: false, connected: true, maState: 'starting', maDetail: 'Unpacking Music Assistant');
    expect(emptyRoomsTitle(starting), 'Your system is still starting…');
    expect(emptyRoomsDetail(starting), 'Unpacking Music Assistant');
    const fresh = PlaybackState(rooms: [], mockMode: false);
    expect(emptyRoomsTitle(fresh), 'Set up your first player');
  });
}
