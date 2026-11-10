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

    return Material(
      color: scheme.surfaceContainerHigh,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          child: Row(
            children: [
              CircleAvatar(
                backgroundColor: scheme.primaryContainer,
                child: Icon(Icons.graphic_eq, color: scheme.onPrimaryContainer),
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
                    Text(
                      subtitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
              IconButton(
                tooltip: np?.isPlaying == true ? 'Pause' : 'Play',
                icon: Icon(np?.isPlaying == true ? Icons.pause_circle : Icons.play_circle),
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
      ),
    );
  }
}
