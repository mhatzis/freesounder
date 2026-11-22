import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../spotify/spotify_store.dart';
import '../spotify/ui/spotify_brand.dart';
import '../spotify/ui/spotify_screen.dart';
import 'library_screen.dart';
import 'podcasts_screen.dart';
import 'radio_screen.dart';
import 'sources_sheet.dart';

/// One tap from the home screen to each music service (0.6.18): connected
/// Spotify first, then radio, the music library and podcasts. No need to go
/// through "Play music" and pick the service there.
class MusicShortcuts extends ConsumerWidget {
  const MusicShortcuts({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final spotifyIn = ref.watch(spotifyProvider.select((s) => s.signedIn));
    Widget chip(String key, Widget avatar, String label, Future<void> Function(BuildContext) open) {
      return ActionChip(
        key: Key('shortcut-$key'),
        avatar: avatar,
        label: Text(label),
        onPressed: () => open(context),
      );
    }

    Icon glyph(String source) => Icon(sourceGlyph(source), size: 18, color: sourceColours[source]);
    return Wrap(
      key: const Key('music-shortcuts'),
      alignment: WrapAlignment.center,
      spacing: 8,
      runSpacing: 8,
      children: [
        if (spotifyIn) chip('spotify', const SpotifyMark.icon(size: 21), 'Spotify', showSpotifyScreen),
        chip('radio', glyph('radio'), 'Radio', showRadioScreen),
        chip('library', glyph('library'), 'Music library', showLibraryScreen),
        chip('podcasts', glyph('podcast'), 'Podcasts', showPodcastsScreen),
        if (!spotifyIn) chip('spotify', const SpotifyMark.icon(size: 21), 'Connect Spotify', showSpotifyScreen),
      ],
    );
  }
}
