import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../spotify/spotify_store.dart';
import '../spotify/ui/spotify_brand.dart';
import '../spotify/ui/spotify_screen.dart';
import '../state/playback_store.dart';
import 'library_screen.dart';
import 'music_nav.dart';
import 'podcasts_screen.dart';
import 'radio_screen.dart';

/// One place to pick what plays: the in-app sources (radio, the home's music
/// library, podcasts, Spotify) and the apps that can cast to a group
/// (AirPlay, UPnP / DLNA). Everything plays through each room's sound
/// settings (bass, treble, loudness, balance).
///
/// Ways out, everywhere in the flow: the ✕ in the header, a swipe down on the
/// sheet (or its handle), a tap outside it, and the system back gesture. A
/// source screen opens on top of the picker, so its back arrow returns here
/// and its ✕ leaves the whole flow ([closeMusic]).
Future<void> showSourcesSheet(BuildContext context) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    isDismissible: true,
    enableDrag: true,
    routeSettings: const RouteSettings(name: '${musicRoutePrefix}picker'),
    builder: (_) => const SourcesSheet(),
  );
}

/// Icon and colour for each source, shared by the picker and now playing.
IconData sourceGlyph(String? source) => switch (source) {
  'radio' => Icons.radio_rounded,
  'library' => Icons.album_rounded,
  'podcast' => Icons.podcasts_rounded,
  'upnp' => Icons.cast_connected_rounded,
  'airplay' => Icons.airplay_rounded,
  'spotify' => Icons.library_music_rounded,
  _ => Icons.music_note_rounded,
};

const sourceColours = <String, Color>{
  'radio': Color(0xFFE53935),
  'library': Color(0xFF3949AB),
  'podcast': Color(0xFF8E24AA),
  'upnp': Color(0xFF00897B),
  'airplay': Color(0xFF546E7A),
};

class SourcesSheet extends ConsumerWidget {
  const SourcesSheet({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final room = ref.watch(playbackProvider.select((s) => s.activeRoom));
    final spotifyIn = ref.watch(spotifyProvider.select((s) => s.signedIn));
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final target = room == null ? null : (room.isGrouped ? (room.groupName ?? room.name) : room.name);
    // Opens on top of the picker: back from the screen comes back here.
    void open(Future<void> Function(BuildContext) screen) => screen(context);

    return SafeArea(
      top: false,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Semantics(
            label: 'Close music picker',
            button: true,
            child: GestureDetector(
              key: const Key('sources-drag-handle'),
              behavior: HitTestBehavior.opaque,
              onTap: () => Navigator.of(context).maybePop(),
              child: SizedBox(
                height: 28,
                child: Center(
                  child: Container(
                    width: 36,
                    height: 4,
                    decoration: BoxDecoration(
                      color: scheme.onSurfaceVariant.withValues(alpha: 0.4),
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                ),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 4, 0),
            child: Row(
              children: [
                Expanded(
                  child: Text('Play music', style: text.titleLarge?.copyWith(fontWeight: FontWeight.w700)),
                ),
                IconButton(
                  key: const Key('sources-close'),
                  tooltip: 'Close',
                  icon: const Icon(Icons.close_rounded),
                  onPressed: () => Navigator.of(context).maybePop(),
                ),
              ],
            ),
          ),
          Flexible(
            child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
              child: Column(
                key: const Key('sources-sheet'),
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    target == null ? 'Pick a room first' : 'In $target',
                    key: const Key('sources-target'),
                    style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                  const SizedBox(height: 16),
                  GridView.count(
                    crossAxisCount: 2,
                    shrinkWrap: true,
                    physics: const NeverScrollableScrollPhysics(),
                    mainAxisSpacing: 12,
                    crossAxisSpacing: 12,
                    childAspectRatio: 1.55,
                    children: [
                      _SourceTile(
                        key: const Key('source-radio'),
                        label: 'Radio',
                        detail: 'Stations worldwide',
                        icon: Icon(sourceGlyph('radio'), color: Colors.white, size: 28),
                        colour: sourceColours['radio']!,
                        onTap: () => open(showRadioScreen),
                      ),
                      _SourceTile(
                        key: const Key('source-library'),
                        label: 'Music library',
                        detail: 'NAS or USB drive',
                        icon: Icon(sourceGlyph('library'), color: Colors.white, size: 28),
                        colour: sourceColours['library']!,
                        onTap: () => open(showLibraryScreen),
                      ),
                      _SourceTile(
                        key: const Key('source-podcasts'),
                        label: 'Podcasts',
                        detail: 'Subscribe and resume',
                        icon: Icon(sourceGlyph('podcast'), color: Colors.white, size: 28),
                        colour: sourceColours['podcast']!,
                        onTap: () => open(showPodcastsScreen),
                      ),
                      _SourceTile(
                        key: const Key('source-spotify'),
                        label: 'Spotify',
                        detail: spotifyIn ? 'Signed in' : 'Spotify Connect',
                        icon: const SpotifyMark.icon(size: 28, onPureBackground: true),
                        colour: const Color(0xFF191414),
                        onTap: () => open(showSpotifyScreen),
                      ),
                    ],
                  ),
                  const SizedBox(height: 20),
                  Text('Cast from another app', style: text.titleSmall),
                  const SizedBox(height: 4),
                  _CastRow(
                    key: const Key('source-airplay-info'),
                    source: 'airplay',
                    title: 'AirPlay',
                    body: target == null
                        ? 'Pick the room in Control Centre or any app’s AirPlay button.'
                        : 'Pick “$target” in Control Centre or any app’s AirPlay button.',
                  ),
                  _CastRow(
                    key: const Key('source-upnp-info'),
                    source: 'upnp',
                    title: 'UPnP / DLNA',
                    body: target == null
                        ? 'Each group is a renderer (with OpenHome) for apps like BubbleUPnP, mconnect or foobar2000.'
                        : '“$target” shows up as a renderer (with OpenHome) in apps like BubbleUPnP, mconnect, '
                              'foobar2000 or Audirvana. Pick it there; it takes over the group like Spotify does.',
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _SourceTile extends StatelessWidget {
  const _SourceTile({
    super.key,
    required this.label,
    required this.detail,
    required this.icon,
    required this.colour,
    required this.onTap,
  });

  final String label;
  final String detail;
  final Widget icon;
  final Color colour;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final hsl = HSLColor.fromColor(colour);
    final end = hsl.withLightness((hsl.lightness * 0.7).clamp(0.0, 1.0)).toColor();
    return Material(
      borderRadius: BorderRadius.circular(18),
      clipBehavior: Clip.antiAlias,
      child: Ink(
        decoration: BoxDecoration(
          gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: [colour, end]),
        ),
        child: InkWell(
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                icon,
                const Spacer(),
                Text(
                  label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 16),
                ),
                Text(
                  detail,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white70, fontSize: 12.5),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _CastRow extends StatelessWidget {
  const _CastRow({super.key, required this.source, required this.title, required this.body});

  final String source;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: CircleAvatar(
        backgroundColor: sourceColours[source]!.withValues(alpha: 0.14),
        child: Icon(sourceGlyph(source), color: sourceColours[source]),
      ),
      title: Text(title),
      subtitle: Text(body, style: TextStyle(color: scheme.onSurfaceVariant)),
    );
  }
}
