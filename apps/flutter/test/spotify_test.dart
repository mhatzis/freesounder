import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/spotify/spotify_api.dart';
import 'package:freesounder/spotify/spotify_auth.dart';
import 'package:freesounder/spotify/spotify_models.dart';
import 'package:freesounder/spotify/spotify_store.dart';
import 'package:freesounder/spotify/ui/spotify_screen.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/now_playing_screen.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _clientId = '0123456789abcdef0123456789abcdef';

void main() {
  test('PKCE sign-in exchanges the code with the verifier and stores tokens', () async {
    final store = MemorySecretStore();
    late Uri authorize;
    late Map<String, String> tokenBody;
    final auth = SpotifyAuth(
      store: store,
      defaultClientId: '',
      authenticate: (url, scheme) async {
        expect(scheme, 'freesounder');
        authorize = Uri.parse(url);
        final state = authorize.queryParameters['state'];
        return 'freesounder://spotify-callback?code=the-code&state=$state';
      },
      httpClient: MockClient((request) async {
        expect(request.url.toString(), 'https://accounts.spotify.com/api/token');
        tokenBody = Uri.splitQueryString(request.body);
        return http.Response(
          jsonEncode({'access_token': 'at-1', 'refresh_token': 'rt-1', 'expires_in': 3600, 'scope': 'streaming'}),
          200,
        );
      }),
    );
    await expectLater(auth.signIn(), throwsA(isA<SpotifyAuthException>()));
    await auth.setClientId(_clientId);
    await auth.signIn();
    expect(authorize.host, 'accounts.spotify.com');
    expect(authorize.queryParameters['client_id'], _clientId);
    expect(authorize.queryParameters['redirect_uri'], SpotifyConfig.customRedirect);
    expect(authorize.queryParameters['code_challenge_method'], 'S256');
    expect(authorize.queryParameters['scope'], contains('streaming'));
    final verifier = tokenBody['code_verifier']!;
    final challenge = base64Url.encode(sha256.convert(utf8.encode(verifier)).bytes).replaceAll('=', '');
    expect(authorize.queryParameters['code_challenge'], challenge);
    expect(tokenBody['grant_type'], 'authorization_code');
    expect(tokenBody['code'], 'the-code');
    expect(tokenBody.containsKey('client_secret'), isFalse);
    expect(await auth.accessToken(), 'at-1');
    await auth.signOut();
    expect(await auth.signedIn, isFalse);
  });

  test('sign-in rejects a callback with the wrong state', () async {
    final auth = SpotifyAuth(
      store: MemorySecretStore(),
      defaultClientId: _clientId,
      authenticate: (url, scheme) async => 'freesounder://spotify-callback?code=x&state=forged',
      httpClient: MockClient((request) async => http.Response('{}', 500)),
    );
    await expectLater(auth.signIn(), throwsA(isA<SpotifyAuthException>()));
    expect(await auth.signedIn, isFalse);
  });

  test('client ID must look like a Spotify Client ID', () async {
    final auth = SpotifyAuth(store: MemorySecretStore(), defaultClientId: '');
    await expectLater(auth.setClientId('not an id'), throwsA(anything));
    expect(await auth.clientId(), isNull);
  });

  Future<SpotifyAuth> signedInAuth() async {
    final store = MemorySecretStore();
    final tokens = SpotifyTokens(
      accessToken: 'at-1',
      refreshToken: 'rt-1',
      expiresAt: DateTime.now().add(const Duration(hours: 1)),
    );
    await store.write('spotify.client_id', _clientId);
    await store.write('spotify.tokens', jsonEncode(tokens.toJson()));
    return SpotifyAuth(store: store, defaultClientId: '');
  }

  test('playlist page reads the 2026 items shape and the old tracks shape', () async {
    final auth = await signedInAuth();
    Map<String, dynamic> playlist(String key, String inner) => {
          'id': 'p1',
          'type': 'playlist',
          'uri': 'spotify:playlist:p1',
          'name': 'Mine',
          'images': [
            {'url': 'https://i.scdn.co/p1', 'width': 640, 'height': 640},
          ],
          key: {
            'items': [
              {
                inner: {
                  'id': 't1',
                  'type': 'track',
                  'uri': 'spotify:track:t1',
                  'name': 'Song',
                  'duration_ms': 200000,
                  'artists': [
                    {'name': 'Band'},
                  ],
                  'album': {'images': []},
                },
              },
            ],
          },
        };
    for (final shape in [('items', 'item'), ('tracks', 'track')]) {
      final api = SpotifyApi(
        auth,
        httpClient: MockClient((request) async {
          expect(request.headers['Authorization'], 'Bearer at-1');
          return http.Response(jsonEncode(playlist(shape.$1, shape.$2)), 200);
        }),
      );
      final page = await api.playlist('p1');
      expect(page.tracks.single.name, 'Song');
      expect(page.tracks.single.contextUri, 'spotify:playlist:p1');
      expect(page.note, isNull);
    }
    final noItems = SpotifyApi(
      auth,
      httpClient: MockClient((request) async {
        final data = playlist('items', 'item')..remove('items');
        return http.Response(jsonEncode(data), 200);
      }),
    );
    final page = await noItems.playlist('p1');
    expect(page.tracks, isEmpty);
    expect(page.note, isNotNull);
  });

  test('Premium errors get a plain message', () async {
    final auth = await signedInAuth();
    final api = SpotifyApi(
      auth,
      httpClient: MockClient(
        (request) async => http.Response(
          jsonEncode({
            'error': {'status': 403, 'message': 'Player command failed: Premium required', 'reason': 'PREMIUM_REQUIRED'},
          }),
          403,
        ),
      ),
    );
    try {
      await api.pause('dev');
      fail('expected an error');
    } on SpotifyApiException catch (err) {
      expect(err.premiumRequired, isTrue);
      expect(err.friendly.toLowerCase(), contains('premium'));
    }
  });

  Widget app({required List overrides, required Widget Function(BuildContext) open}) => ProviderScope(
        overrides: [
          appConfigProvider.overrideWithValue(const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero)),
          ...overrides.cast(),
        ],
        child: MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(body: Center(child: open(context))),
          ),
        ),
      );

  testWidgets('setup guide explains the developer app and saves a Client ID', (tester) async {
    tester.view.physicalSize = const Size(500, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final store = MemorySecretStore();
    await tester.pumpWidget(
      app(
        overrides: [
          secretStoreProvider.overrideWithValue(store),
          spotifyAuthProvider.overrideWith((ref) => SpotifyAuth(store: store, defaultClientId: '')),
        ],
        open: (context) => TextButton(onPressed: () => showSpotifyScreen(context), child: const Text('open')),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('spotify-setup')), findsOneWidget);
    expect(find.text(SpotifyConfig.customRedirect), findsOneWidget);
    expect(find.byKey(const Key('spotify-sign-in')), findsNothing);
    await tester.tap(find.byKey(const Key('spotify-loopback')));
    await tester.pumpAndSettle();
    expect(find.text(SpotifyConfig.loopbackRedirect), findsOneWidget);
    await tester.enterText(find.byKey(const Key('spotify-client-id')), _clientId);
    await tester.tap(find.byKey(const Key('spotify-save-client-id')));
    await tester.pumpAndSettle();
    expect(store.values['spotify.client_id'], _clientId);
    expect(find.byKey(const Key('spotify-sign-in')), findsOneWidget);
  });

  testWidgets('demo play shows the track on the now-playing screen', (tester) async {
    tester.view.physicalSize = const Size(500, 1000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      app(
        overrides: [secretStoreProvider.overrideWithValue(MemorySecretStore())],
        open: (context) => TextButton(onPressed: () => showNowPlayingScreen(context), child: const Text('open')),
      ),
    );
    await tester.pumpAndSettle();
    final container = ProviderScope.containerOf(tester.element(find.text('open')));
    final targets = playTargets(container.read(playbackProvider).rooms);
    expect(targets, isNotEmpty);
    await container.read(spotifyProvider.notifier).play(
          targets.first.id,
          const SpotifyItem(
            type: 'track',
            id: 't1',
            uri: 'spotify:track:t1',
            name: 'Demo Song',
            subtitle: 'Demo Band',
            durationMs: 180000,
          ),
        );
    container.read(playbackProvider.notifier).selectRoom(targets.first.memberIds.first);
    await tester.pump();
    await tester.tap(find.text('open'));
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pump(const Duration(milliseconds: 500));
    expect(find.text('Demo Song'), findsWidgets);
    expect(find.textContaining('Demo Band'), findsWidgets);
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 1));
  });
}
