import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/control_client.dart';
import 'package:freesounder/models/library.dart';
import 'package:freesounder/models/podcast.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/app_shell.dart' show sourceLabel;
import 'package:freesounder/ui/sources_sheet.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Future<ProviderContainer> _pumpDemo(WidgetTester tester) async {
  tester.view.physicalSize = const Size(600, 1300);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        appConfigProvider.overrideWithValue(const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero)),
      ],
      child: MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(onPressed: () => showSourcesSheet(context), child: const Text('open')),
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
  return ProviderScope.containerOf(tester.element(find.byType(Scaffold).first));
}

Future<void> _pumpFrames(WidgetTester tester, [int n = 10]) async {
  for (var i = 0; i < n; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

void main() {
  testWidgets('source picker lists every source and how to cast with UPnP / AirPlay', (tester) async {
    await _pumpDemo(tester);
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    for (final key in ['source-radio', 'source-library', 'source-podcasts', 'source-spotify']) {
      expect(find.byKey(Key(key)), findsOneWidget, reason: key);
    }
    expect(find.byKey(const Key('source-upnp-info')), findsOneWidget);
    expect(find.textContaining('BubbleUPnP'), findsOneWidget);
    expect(find.byKey(const Key('source-airplay-info')), findsOneWidget);
  });

  testWidgets('library: browse an album and play it in the room', (tester) async {
    final container = await _pumpDemo(tester);
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('source-library')));
    await tester.pumpAndSettle();
    expect(find.text('Music library'), findsOneWidget);
    expect(find.byKey(const Key('library-album-Blue Hour')), findsOneWidget);
    await tester.tap(find.byKey(const Key('library-album-Blue Hour')));
    await tester.pumpAndSettle();
    expect(find.text('Slow River'), findsOneWidget);
    await tester.tap(find.text('Slow River'));
    await _pumpFrames(tester);
    final np = container.read(playbackProvider).nowPlaying;
    expect(np?.source, 'library');
    expect(np?.title, 'Slow River');
    expect(sourceLabel(np?.source), 'Music library');
  });

  testWidgets('library search and folders', (tester) async {
    await _pumpDemo(tester);
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('source-library')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('library-search')), 'neon');
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('library-track-demo/night/01.flac')), findsOneWidget);
    await tester.enterText(find.byKey(const Key('library-search')), '');
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Folders'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('library-folder-Night Drive')), findsOneWidget);
  });

  testWidgets('podcasts: find, subscribe, resume where the home left off', (tester) async {
    final container = await _pumpDemo(tester);
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('source-podcasts')));
    await tester.pumpAndSettle();
    expect(find.text('Find something to listen to'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('podcast-search')), 'demo');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Demo Talk'));
    await tester.pumpAndSettle();
    expect(find.text('Subscribe'), findsOneWidget);
    await tester.tap(find.byKey(const Key('podcast-subscribe')));
    await tester.pumpAndSettle();
    expect(find.text('Subscribed'), findsOneWidget);
    expect(
      find.byKey(const Key('podcast-progress-ep2')),
      findsOneWidget,
      reason: 'half-listened episode shows progress',
    );
    expect(find.textContaining('20 min left'), findsOneWidget);
    expect(find.textContaining('Played'), findsOneWidget, reason: 'finished episode is marked');
    await tester.tap(find.byKey(const Key('podcast-episode-ep2')));
    await _pumpFrames(tester);
    final np = container.read(playbackProvider).nowPlaying;
    expect(np?.source, 'podcast');
    expect(np?.position, 600, reason: 'resumes at the shared position');
    // Back on the podcasts home, the subscription is listed.
    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('podcast-search')), '');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('podcast-sub-https://example.com/demo.xml')), findsOneWidget);
  });

  test('client: library and podcast requests hit the right endpoints', () async {
    final seen = <String>[];
    final bodies = <String, Object?>{};
    final mock = MockClient((request) async {
      final key = '${request.method} ${request.url.path}${request.url.hasQuery ? '?${request.url.query}' : ''}';
      seen.add(key);
      if (request.body.isNotEmpty) {
        bodies[key] = jsonDecode(request.body);
      }
      return switch (request.url.path) {
        '/api/v2/library' => http.Response(
          jsonEncode({
            'shares': [
              {
                'id': 'smb-1',
                'kind': 'smb',
                'host': 'nas',
                'share': 'music',
                'username': 'me',
                'has_password': true,
                'mounted': true,
              },
            ],
            'stats': {'songs': 12, 'albums': 2, 'artists': 1},
            'scanning': false,
            'host_id': 'p1',
            'host_room': 'Kitchen',
          }),
          200,
        ),
        '/api/v2/library/shares' => http.Response(
          jsonEncode({
            'share': {'id': 'smb-2', 'kind': 'smb', 'host': 'nas', 'share': 'more', 'has_password': true},
          }),
          200,
        ),
        _ => http.Response('{"ok":true}', 200),
      };
    });
    final client = ControlApiClient(baseUrl: 'http://10.0.0.2:8080', httpClient: mock);
    final summary = await client.librarySummary();
    expect(summary.songs, 12);
    expect(summary.hostRoom, 'Kitchen');
    expect(summary.shares.single.hasPassword, isTrue);
    expect(summary.shares.single.address, r'\\nas\music');
    final share = await client.addLibraryShare({'kind': 'smb', 'host': 'nas', 'share': 'more', 'password': 'pw'});
    expect(share.id, 'smb-2');
    const tracks = [LibraryTrack(uri: 'smb-1/a.flac', title: 'A'), LibraryTrack(uri: 'smb-1/b.flac', title: 'B')];
    await client.playLibrary('patio', tracks, start: 1, contextName: 'Album');
    final play = bodies['POST /api/v2/groups/patio/library/play']! as Map;
    expect(play['start'], 1);
    expect((play['tracks'] as List).length, 2);
    await client.unsubscribePodcast('https://x.example/feed?id=1&a=b');
    expect(seen.last, 'DELETE /api/v2/podcasts/subscriptions?feed=https%3A%2F%2Fx.example%2Ffeed%3Fid%3D1%26a%3Db');
    const ep = PodcastEpisode(id: 'e1', title: 'E', url: 'https://x.example/e.mp3', duration: 60);
    await client.playPodcast('patio', ep, const Podcast(feed: 'https://x.example/feed', title: 'P'));
    final pod = bodies['POST /api/v2/groups/patio/podcast/play']! as Map;
    expect(pod.containsKey('position'), isFalse, reason: 'no position = resume where the home left off');
    expect((pod['episode'] as Map)['url'], 'https://x.example/e.mp3');
    expect(
      client.libraryArtUrl(album: 'Blue Hour', artist: 'A&B'),
      'http://10.0.0.2:8080/api/v2/library/art?album=Blue+Hour&artist=A%26B',
    );
  });

  test('episode progress and lengths', () {
    expect(formatEpisodeLength(3900), '1 h 05 min');
    expect(formatEpisodeLength(42 * 60), '42 min');
    const ep = PodcastEpisode(id: 'a', title: 't', url: 'u', duration: 100, position: 50);
    expect(ep.progress, 0.5);
    expect(ep.canResume, isTrue);
    const fresh = PodcastEpisode(id: 'b', title: 't', url: 'u', duration: 100, position: 5);
    expect(fresh.canResume, isFalse);
    expect(fresh.progress, isNull);
  });
}
