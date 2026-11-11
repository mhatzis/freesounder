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

/// A fake LAN: one or more boards answering agent (8090) and control API (8080).
class FakeLan {
  final requests = <http.Request>[];
  var agentFailuresLeft = 0;
  int? adoptStatus;
  String adoptBody = '{"detail":"boom"}';
  int? controlAdoptStatus;

  MockClient get client => MockClient((request) async {
        requests.add(request);
        final path = request.url.path;
        final port = request.url.port;
        if (port == 8090 && path == '/api/v1/agent') {
          if (agentFailuresLeft > 0) {
            agentFailuresLeft--;
            throw const SocketException('Local network prohibited');
          }
          return http.Response(jsonEncode({'id': 'pi-1', 'name': 'fs-new', 'state': 'unconfigured'}), 200);
        }
        if (port == 8090 && path == '/api/v1/agent/adopt') {
          if (adoptStatus != null) {
            return http.Response(adoptBody, adoptStatus!);
          }
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          return http.Response(jsonEncode({'id': 'pi-1', 'name': body['name'], 'role': body['role'], 'state': 'configured'}), 200);
        }
        if (port == 8080 && path == '/health') {
          return http.Response(jsonEncode({'status': 'degraded', 'ma_connected': false, 'ma_state': 'starting', 'ma_detail': 'Unpacking Music Assistant', 'version': '0.3.0'}), 200);
        }
        if (port == 8080 && path == '/api/v1/rooms') {
          return http.Response('[]', 200);
        }
        if (port == 8080 && path == '/api/v1/players/adopt') {
          if (controlAdoptStatus != null) {
            return http.Response('{"detail":"Player agent error: timed out"}', controlAdoptStatus!);
          }
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          return http.Response(jsonEncode({'id': body['id'] ?? 'x', 'name': body['name'], 'url': body['url'] ?? '', 'role': 'room', 'state': 'configured'}), 200);
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
    test('adopts the board as host by IP and waits for its control API', () async {
      final lan = FakeLan()..agentFailuresLeft = 2; // e.g. the local-network prompt
      final progress = <String>[];
      final base = await fastSetup(lan.client).createSystem(ip: '192.168.1.24', name: 'Lounge', onProgress: progress.add);
      expect(base, 'http://192.168.1.24:8080');
      final adopt = lan.to('/api/v1/agent/adopt').single;
      expect(adopt.url.toString(), 'http://192.168.1.24:8090/api/v1/agent/adopt');
      expect(jsonDecode(adopt.body), {'name': 'Lounge', 'host': '192.168.1.24', 'role': 'host'});
      expect(lan.to('/api/v1/agent').length, 3);
      expect(lan.to('/health'), isNotEmpty);
      expect(progress.first, contains('192.168.1.24'));
    });

    test('names the step and HTTP status when the player refuses', () async {
      final lan = FakeLan()..adoptStatus = 500;
      final failure = await fastSetup(lan.client)
          .createSystem(ip: '192.168.1.24', name: 'Lounge')
          .then<AdoptFailure?>((_) => null, onError: (Object e) => e as AdoptFailure);
      expect(failure!.step, 'Creating your system');
      expect(failure.statusCode, 500);
      expect(failure.reason, 'boom');
      expect(failure.message, contains('Creating your system failed (HTTP 500)'));
      expect(failure.debugLine, contains('http://192.168.1.24:8090/api/v1/agent/adopt -> HTTP 500: boom'));
    });

    test('reports the network exception when the player never answers', () async {
      final lan = FakeLan()..agentFailuresLeft = 1000;
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
    test('first player: no system yet, so it creates one and connects to it', () async {
      final lan = FakeLan();
      final container = containerFor(lan);
      await settle(container);
      expect(container.read(playbackProvider).connected, isFalse);

      await container.read(playbackProvider.notifier).adoptProvisioned(
            name: 'Lounge',
            id: 'pi-1',
            ip: '192.168.1.24',
            url: 'http://192.168.1.24:8090',
          );
      final state = container.read(playbackProvider);
      expect(state.connected, isTrue);
      expect(state.hostUrl, 'http://192.168.1.24:8080');
      expect(state.systemStarting, isTrue);
      expect(state.maDetail, 'Unpacking Music Assistant');
      expect(jsonDecode(lan.to('/api/v1/agent/adopt').single.body)['role'], 'host');
      expect(lan.to('/api/v1/players/adopt'), isEmpty);
    });

    test('join existing: connected to a host, a new board is adopted as a room', () async {
      final lan = FakeLan();
      final container = containerFor(lan);
      await settle(container);
      final store = container.read(playbackProvider.notifier);
      expect(await store.connectTo('http://192.168.1.10:8080', persist: false), isTrue);

      await store.adoptProvisioned(name: 'Kitchen', id: 'pi-2', ip: '192.168.1.77', url: 'http://192.168.1.77:8090');
      final adopt = lan.to('/api/v1/players/adopt').single;
      expect(adopt.url.host, '192.168.1.10');
      final body = jsonDecode(adopt.body) as Map<String, dynamic>;
      expect(body, {'name': 'Kitchen', 'id': 'pi-2', 'url': 'http://192.168.1.77:8090'});
      expect(body.containsKey('role'), isFalse, reason: 'the host decides; it never demotes itself');
      expect(lan.to('/api/v1/agent/adopt'), isEmpty);
    });

    test('connected to the very board being added: create, not join', () async {
      final lan = FakeLan();
      final container = containerFor(lan);
      await settle(container);
      final store = container.read(playbackProvider.notifier);
      await store.connectTo('http://192.168.1.24:8080', persist: false);
      await store.adoptProvisioned(name: 'Lounge', id: 'pi-1', ip: '192.168.1.24');
      expect(jsonDecode(lan.to('/api/v1/agent/adopt').single.body)['role'], 'host');
      expect(lan.to('/api/v1/players/adopt'), isEmpty);
    });

    test('join failure names the step and status', () async {
      final lan = FakeLan()..controlAdoptStatus = 502;
      final container = containerFor(lan);
      await settle(container);
      final store = container.read(playbackProvider.notifier);
      await store.connectTo('http://192.168.1.10:8080', persist: false);
      final failure = await store
          .adoptProvisioned(name: 'Kitchen', id: 'pi-2', ip: '192.168.1.77', url: 'http://192.168.1.77:8090')
          .then<AdoptFailure?>((_) => null, onError: (Object e) => e as AdoptFailure);
      expect(failure!.step, 'Adding it to your system');
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
    final old = ControlApiException('HTTP 502: Music Assistant is not reachable', statusCode: 502, detail: 'Music Assistant is not reachable');
    expect(userMessageFor(old), startsWith('Your system is still starting…'));
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
