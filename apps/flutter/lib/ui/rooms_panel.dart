import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/room.dart';
import '../state/playback_store.dart';
import '../theme/tokens.dart';

/// Rooms list used both in the bottom sheet and the wide right-hand rail.
class RoomsPanel extends ConsumerWidget {
  const RoomsPanel({super.key, this.asRail = false});

  final bool asRail;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(playbackProvider);
    final rooms = state.rooms.where((room) => !room.isGroup).toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
          child: Row(
            children: [
              Text('Rooms', style: Theme.of(context).textTheme.titleLarge),
              const Spacer(),
              if (state.mockMode)
                const Chip(label: Text('Mock data'), visualDensity: VisualDensity.compact),
              if (!state.connected && !state.mockMode)
                const Chip(label: Text('Offline'), visualDensity: VisualDensity.compact),
            ],
          ),
        ),
        Expanded(
          child: ListView.builder(
            itemCount: rooms.length,
            itemBuilder: (context, index) => _RoomTile(room: rooms[index]),
          ),
        ),
      ],
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
    return ListTile(
      selected: selected,
      leading: Icon(selected ? Icons.speaker : Icons.speaker_outlined),
      title: Text(room.name),
      subtitle: Text(
        [
          if (room.volume != null) 'Vol ${room.volume}',
          if (room.groupId != null) 'Grouped',
          if (room.state != null) room.state!,
        ].join(' · '),
      ),
      trailing: SizedBox(
        width: 128,
        child: Slider(
          value: (room.volume ?? 0).clamp(0, 100).toDouble(),
          max: 100,
          label: '${room.volume ?? 0}',
          onChanged: (value) =>
              ref.read(playbackProvider.notifier).setVolume(room.id, value.round()),
        ),
      ),
      onTap: () => ref.read(playbackProvider.notifier).selectRoom(room.id),
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
          elevation: 8,
          borderRadius: const BorderRadius.vertical(top: Radius.circular(16)),
          clipBehavior: Clip.antiAlias,
          child: CustomScrollView(
            controller: scrollController,
            slivers: [
              const SliverToBoxAdapter(child: _Grabber()),
              SliverFillRemaining(
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
      padding: const EdgeInsets.only(top: 8, bottom: 4),
      child: Center(
        child: Container(
          width: 40,
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
