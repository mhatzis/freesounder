import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/playback_store.dart';

/// Persistent mini-player: now-playing, play/pause, and a rooms chip.
class MiniPlayer extends ConsumerWidget {
  const MiniPlayer({super.key, this.onRoomsPressed, this.showRoomsChip = true});

  final VoidCallback? onRoomsPressed;
  final bool showRoomsChip;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(playbackProvider);
    final room = state.activeRoom;
    final np = state.nowPlaying;
    final scheme = Theme.of(context).colorScheme;
    final title = np?.title ?? 'Nothing playing';
    final subtitle = [
      if (np?.artist != null) np!.artist,
      if (room != null) room.name,
    ].join(' · ');
    final progress = (np?.duration != null && np!.duration! > 0)
        ? ((np.position ?? 0) / np.duration!).clamp(0.0, 1.0)
        : 0.0;

    return Material(
      color: scheme.surfaceContainerHigh,
      elevation: 0,
      child: SafeArea(
        top: false,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            LinearProgressIndicator(
              value: progress,
              minHeight: 2,
              backgroundColor: Colors.transparent,
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 10, 8, 10),
              child: Row(
                children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(8),
                    child: SizedBox(
                      width: 40,
                      height: 40,
                      child: Image.asset('assets/logo.png', fit: BoxFit.cover),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.titleSmall,
                        ),
                        if (subtitle.isNotEmpty)
                          Text(
                            subtitle,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                              color: scheme.onSurfaceVariant,
                            ),
                          ),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: np?.isPlaying == true ? 'Pause' : 'Play',
                    icon: Icon(
                      np?.isPlaying == true ? Icons.pause_circle : Icons.play_circle,
                      size: 36,
                    ),
                    onPressed: () => ref.read(playbackProvider.notifier).togglePlayPause(),
                  ),
                  IconButton(
                    tooltip: 'Next',
                    icon: const Icon(Icons.skip_next),
                    onPressed: () => ref.read(playbackProvider.notifier).skipNext(),
                  ),
                  if (showRoomsChip)
                    ActionChip(
                      key: const Key('rooms-chip'),
                      tooltip: 'Rooms',
                      avatar: const Icon(Icons.speaker_group, size: 18),
                      label: Text(room?.name ?? 'Rooms'),
                      onPressed: onRoomsPressed,
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
