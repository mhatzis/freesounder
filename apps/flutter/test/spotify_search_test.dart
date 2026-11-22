import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/spotify/spotify_api.dart';
import 'package:freesounder/spotify/spotify_models.dart';
import 'package:freesounder/state/playback_store.dart';
import 'package:freesounder/ui/music_shortcuts.dart';

SpotifyItem _item(String type, String name) =>
    SpotifyItem(type: type, id: '$type-$name', uri: 'spotify:$type:$name', name: name);

void main() {
  test('search covers podcasts and episodes too', () {
    expect(SpotifyApi.searchTypes.keys, containsAll(['track', 'artist', 'album', 'playlist', 'show', 'episode']));
  });

  test('top result: an artist named exactly as typed wins, else the first song', () {
    final sections = [
      SpotifySection('Songs', [_item('track', 'Yellow')]),
      SpotifySection('Artists', [_item('artist', 'Coldplay')]),
    ];
    expect(SpotifyApi.topResult('coldplay', sections)!.type, 'artist');
    expect(SpotifyApi.topResult('yellow', sections)!.name, 'Yellow');
    final podcast = [
      SpotifySection('Songs', [_item('track', 'Something')]),
      SpotifySection('Podcasts', [_item('show', 'The Daily')]),
    ];
    expect(SpotifyApi.topResult('the daily', podcast)!.type, 'show');
    expect(SpotifyApi.topResult('x', const []), isNull);
  });

  testWidgets('home shortcuts reach each music service', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          appConfigProvider.overrideWithValue(const AppConfig(forceDemo: true, discoveryTimeout: Duration.zero)),
        ],
        child: const MaterialApp(home: Scaffold(body: Center(child: MusicShortcuts()))),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('shortcut-radio')), findsOneWidget);
    expect(find.byKey(const Key('shortcut-library')), findsOneWidget);
    expect(find.byKey(const Key('shortcut-podcasts')), findsOneWidget);
    expect(find.text('Connect Spotify'), findsOneWidget); // not signed in
  });
}
