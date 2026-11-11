import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/errors.dart';
import '../models/now_playing.dart';
import '../spotify/ui/spotify_screen.dart';
import '../state/playback_store.dart';
import 'artwork.dart';
import 'radio_screen.dart';

Future<void> showNowPlayingScreen(BuildContext context) {
  return Navigator.of(context).push(
    MaterialPageRoute<void>(builder: (_) => const NowPlayingScreen(), fullscreenDialog: true),
  );
}

String? sourceName(String? source) => switch (source) {
      'radio' => 'Radio',
      'spotify' => 'Spotify',
      'airplay' => 'AirPlay',
      _ => null,
    };

IconData sourceIcon(String? source) => switch (source) {
      'radio' => Icons.radio,
      'spotify' => Icons.library_music,
      'airplay' => Icons.airplay,
      _ => Icons.music_note,
    };

/// Full-screen now playing for the active room's group.
class NowPlayingScreen extends ConsumerStatefulWidget {
  const NowPlayingScreen({super.key});

  @override
  ConsumerState<NowPlayingScreen> createState() => _NowPlayingScreenState();
}

class _NowPlayingScreenState extends ConsumerState<NowPlayingScreen> {
  double? _dragging;
  double? _volume;

  Future<void> _run(Future<void> Function() action) async {
    try {
      await action();
    } on Object catch (err) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(userMessageFor(err))));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(playbackProvider);
    final store = ref.read(playbackProvider.notifier);
    final room = state.activeRoom;
    final np = state.nowPlaying ?? (room == null ? null : store.nowPlayingFor(room.id));
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final where = room == null ? '' : (room.isGrouped ? (room.groupName ?? room.name) : room.name);
    final subtitle = [np?.artist, np?.album ?? np?.contextName].whereType<String>().where((s) => s.isNotEmpty).join(' — ');
    final station = np?.source == 'radio' ? np?.stationName : null;
    final canSeek = np?.source == 'spotify' && (np?.duration ?? 0) > 0;
    final volume = _volume ?? (room?.volume ?? 0).toDouble();

    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          tooltip: 'Close',
          icon: const Icon(Icons.keyboard_arrow_down),
          onPressed: () => Navigator.of(context).pop(),
        ),
        title: Column(
          children: [
            Text('Playing in', style: text.labelSmall),
            Text(where, key: const Key('np-screen-room'), style: text.titleSmall),
          ],
        ),
        centerTitle: true,
      ),
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) {
            final art = (constraints.maxWidth - 48).clamp(120.0, 440.0);
            return ListView(
              padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
              children: [
                Center(
                  child: SizedBox(
                    width: art,
                    height: art,
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(24),
                      child: Artwork(
                        key: const Key('np-screen-art'),
                        urls: np?.artworkCandidates ?? const [],
                        icon: np?.source == 'radio' ? Icons.radio : null,
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 24),
                Text(
                  np?.hasTrack == true ? np!.title! : 'Nothing playing',
                  key: const Key('np-screen-title'),
                  textAlign: TextAlign.center,
                  style: text.headlineSmall?.copyWith(fontWeight: FontWeight.w600),
                ),
                if (subtitle.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text(subtitle, textAlign: TextAlign.center, style: text.bodyLarge?.copyWith(color: scheme.onSurfaceVariant)),
                ],
                if (station != null && station != np?.artist) ...[
                  const SizedBox(height: 4),
                  Text(station, textAlign: TextAlign.center, style: text.bodyMedium),
                ],
                if (sourceName(np?.source) != null) ...[
                  const SizedBox(height: 10),
                  Center(
                    child: Chip(
                      key: const Key('np-screen-source'),
                      avatar: Icon(sourceIcon(np?.source), size: 16),
                      label: Text(sourceName(np?.source)!),
                    ),
                  ),
                ],
                const SizedBox(height: 12),
                if (np != null && (np.duration ?? 0) > 0)
                  Ticking(
                    active: np.isPlaying && _dragging == null,
                    builder: (context) => _Progress(
                      np: np,
                      dragging: _dragging,
                      canSeek: canSeek,
                      onChanged: (v) => setState(() => _dragging = v),
                      onChangeEnd: (v) {
                        setState(() => _dragging = null);
                        _run(() => store.seek(v));
                      },
                    ),
                  ),
                const SizedBox(height: 8),
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    IconButton(
                      key: const Key('np-screen-prev'),
                      iconSize: 36,
                      tooltip: 'Previous',
                      onPressed: np?.source == 'radio' ? null : () => _run(store.skipPrevious),
                      icon: const Icon(Icons.skip_previous),
                    ),
                    const SizedBox(width: 12),
                    IconButton.filled(
                      key: const Key('np-screen-play'),
                      iconSize: 44,
                      tooltip: np?.isPlaying == true ? 'Pause' : 'Play',
                      onPressed: () => _run(store.togglePlayPause),
                      icon: Icon(np?.isPlaying == true ? Icons.pause : Icons.play_arrow),
                    ),
                    const SizedBox(width: 12),
                    IconButton(
                      key: const Key('np-screen-next'),
                      iconSize: 36,
                      tooltip: 'Next',
                      onPressed: np?.source == 'radio' ? null : () => _run(store.skipNext),
                      icon: const Icon(Icons.skip_next),
                    ),
                  ],
                ),
                if (room != null) ...[
                  const SizedBox(height: 12),
                  Row(
                    children: [
                      const Icon(Icons.volume_down),
                      Expanded(
                        child: Slider(
                          key: const Key('np-screen-volume'),
                          value: volume.clamp(0, 100),
                          max: 100,
                          onChanged: (v) => setState(() => _volume = v),
                          onChangeEnd: (v) {
                            setState(() => _volume = null);
                            _run(() => room.isGrouped
                                ? store.setGroupVolume(room.id, v.round())
                                : store.setVolume(room.id, v.round()));
                          },
                        ),
                      ),
                      const Icon(Icons.volume_up),
                    ],
                  ),
                ],
                const SizedBox(height: 12),
                Wrap(
                  alignment: WrapAlignment.center,
                  spacing: 12,
                  children: [
                    OutlinedButton.icon(
                      key: const Key('np-screen-spotify'),
                      onPressed: () => showSpotifyScreen(context),
                      icon: const Icon(Icons.library_music),
                      label: const Text('Spotify'),
                    ),
                    OutlinedButton.icon(
                      onPressed: () => showRadioScreen(context),
                      icon: const Icon(Icons.radio),
                      label: const Text('Radio'),
                    ),
                  ],
                ),
              ],
            );
          },
        ),
      ),
    );
  }
}

class _Progress extends StatelessWidget {
  const _Progress({
    required this.np,
    required this.dragging,
    required this.canSeek,
    required this.onChanged,
    required this.onChangeEnd,
  });

  final NowPlaying np;
  final double? dragging;
  final bool canSeek;
  final ValueChanged<double> onChanged;
  final ValueChanged<double> onChangeEnd;

  @override
  Widget build(BuildContext context) {
    final total = np.duration!;
    final position = (dragging ?? np.livePosition() ?? 0).clamp(0.0, total);
    final text = Theme.of(context).textTheme.labelSmall;
    return Column(
      children: [
        Slider(
          key: const Key('np-screen-progress'),
          value: position,
          max: total,
          onChanged: canSeek ? onChanged : null,
          onChangeEnd: canSeek ? onChangeEnd : null,
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Row(
            children: [
              Text(formatDuration(position), key: const Key('np-screen-position'), style: text),
              const Spacer(),
              Text(formatDuration(total), style: text),
            ],
          ),
        ),
      ],
    );
  }
}
