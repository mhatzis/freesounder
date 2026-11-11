import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/room.dart';
import '../state/playback_store.dart';
import '../theme/tokens.dart';
import 'add_player_sheet.dart';
import 'room_settings_sheet.dart';

/// Rooms list used both in the bottom sheet and the wide right-hand rail.
class RoomsPanel extends ConsumerWidget {
  const RoomsPanel({super.key, this.asRail = false});

  final bool asRail;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(playbackProvider);
    final rooms = state.rooms.where((room) => !room.isGroup).toList();
    final scheme = Theme.of(context).colorScheme;

    return ColoredBox(
      color: asRail ? scheme.surfaceContainerLow : scheme.surface,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: EdgeInsets.fromLTRB(20, asRail ? 20 : 8, 16, 8),
            child: Row(
              children: [
                Text('Rooms', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w600)),
                const Spacer(),
                if (state.mockMode)
                  Text(
                    'Demo mode',
                    style: Theme.of(context).textTheme.labelSmall?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                if (!state.connected && !state.mockMode && !state.discovering)
                  Text(
                    'Offline',
                    style: Theme.of(context).textTheme.labelSmall?.copyWith(color: scheme.error),
                  ),
                IconButton(
                  key: const Key('add-player-button'),
                  tooltip: 'Add a player',
                  icon: const Icon(Icons.add),
                  onPressed: () => showAddPlayerSheet(context),
                ),
              ],
            ),
          ),
          Expanded(
            child: rooms.isEmpty
                ? Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Text(
                          state.discovering
                              ? 'Looking for players…'
                              : (state.mockMode ? 'No rooms yet' : 'Set up your first player'),
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.bodyLarge,
                        ),
                        if (!state.discovering && !state.mockMode) ...[
                          const SizedBox(height: 16),
                          FilledButton(
                            key: const Key('rooms-setup-add-player'),
                            onPressed: () => showAddPlayerSheet(context),
                            child: const Text('Add a player'),
                          ),
                          TextButton(
                            key: const Key('rooms-setup-try-demo'),
                            onPressed: () => ref.read(playbackProvider.notifier).useDemo(),
                            child: const Text('Try demo'),
                          ),
                        ],
                      ],
                    ),
                  )
                : ListView.separated(
                    padding: const EdgeInsets.fromLTRB(12, 4, 12, 24),
                    itemCount: rooms.length,
                    separatorBuilder: (_, _) => const SizedBox(height: 4),
                    itemBuilder: (context, index) => _RoomTile(room: rooms[index]),
                  ),
          ),
        ],
      ),
    );
  }
}

class _RoomTile extends ConsumerWidget {
  const _RoomTile({required this.room});

  final Room room;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(playbackProvider);
    final selected = state.activeRoomId == room.id;
    final scheme = Theme.of(context).colorScheme;
    return Material(
      color: selected ? scheme.primaryContainer.withValues(alpha: 0.55) : Colors.transparent,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: () => ref.read(playbackProvider.notifier).selectRoom(room.id),
        onLongPress: () => showRoomSettingsSheet(context, roomId: room.id, roomName: room.name),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 8, 6),
          child: Column(
            children: [
              Row(
                children: [
                  Icon(
                    selected ? Icons.speaker : Icons.speaker_outlined,
                    color: selected ? scheme.primary : scheme.onSurfaceVariant,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      room.name,
                      style: Theme.of(context).textTheme.titleSmall?.copyWith(
                        fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
                      ),
                    ),
                  ),
                  Text(
                    [
                      if (room.volume != null) '${room.volume}',
                      if (room.groupId != null) 'Grouped',
                    ].join(' · '),
                    style: Theme.of(context).textTheme.labelSmall?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                  IconButton(
                    key: Key('room-settings-${room.id}'),
                    tooltip: 'Room settings',
                    icon: const Icon(Icons.tune, size: 20),
                    onPressed: () => showRoomSettingsSheet(context, roomId: room.id, roomName: room.name),
                  ),
                ],
              ),
              Slider(
                value: (room.volume ?? 0).clamp(0, 100).toDouble(),
                max: 100,
                label: '${room.volume ?? 0}',
                onChanged: (value) =>
                    ref.read(playbackProvider.notifier).setVolume(room.id, value.round()),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Draggable bottom sheet with collapsed / half / full snap detents.
class RoomsBottomSheet extends ConsumerWidget {
  const RoomsBottomSheet({super.key, required this.controller});

  final DraggableScrollableController controller;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return DraggableScrollableSheet(
      controller: controller,
      initialChildSize: FsTokens.sheetCollapsed,
      minChildSize: FsTokens.sheetCollapsed,
      maxChildSize: FsTokens.sheetFull,
      snap: true,
      snapSizes: const [FsTokens.sheetCollapsed, FsTokens.sheetHalf, FsTokens.sheetFull],
      builder: (context, scrollController) {
        return Material(
          elevation: 1,
          color: Theme.of(context).colorScheme.surfaceContainerLow,
          borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
          clipBehavior: Clip.antiAlias,
          child: CustomScrollView(
            controller: scrollController,
            slivers: [
              const SliverToBoxAdapter(child: _Grabber()),
              const SliverFillRemaining(
                hasScrollBody: true,
                child: RoomsPanel(),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _Grabber extends StatelessWidget {
  const _Grabber();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 10, bottom: 4),
      child: Center(
        child: Container(
          width: 36,
          height: 4,
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.outlineVariant,
            borderRadius: BorderRadius.circular(2),
          ),
        ),
      ),
    );
  }
}
