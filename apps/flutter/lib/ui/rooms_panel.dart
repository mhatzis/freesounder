import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/room.dart';
import '../state/playback_store.dart';
import '../theme/tokens.dart';
import 'add_player_sheet.dart';
import 'artwork.dart';
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
                  Text('Offline', style: Theme.of(context).textTheme.labelSmall?.copyWith(color: scheme.error)),
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
                          emptyRoomsTitle(state),
                          key: const Key('rooms-empty-title'),
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.bodyLarge,
                        ),
                        if (state.connected && !state.mockMode && !state.discovering) ...[
                          const SizedBox(height: 8),
                          Text(
                            emptyRoomsDetail(state),
                            key: const Key('rooms-empty-detail'),
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                          if (state.systemStarting) ...[
                            const SizedBox(height: 12),
                            const SizedBox(width: 160, child: LinearProgressIndicator()),
                          ],
                        ],
                        if (!state.discovering && !state.mockMode && !state.connected) ...[
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
    final np = ref.read(playbackProvider.notifier).nowPlayingFor(room.id);
    final nowTitle = np?.hasTrack == true ? np!.title : room.nowTitle;
    final nowArtist = np?.hasTrack == true ? np!.artist : room.nowArtist;
    final art = [...?np?.artworkCandidates, ?room.nowImageUrl];
    final nowLine = [?nowTitle, ?nowArtist].where((s) => s.isNotEmpty).join(' · ');
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
                  if (art.isNotEmpty || nowLine.isNotEmpty)
                    ClipRRect(
                      key: Key('room-art-${room.id}'),
                      borderRadius: BorderRadius.circular(6),
                      child: SizedBox(width: 36, height: 36, child: Artwork(urls: art)),
                    )
                  else
                    Icon(
                      selected ? Icons.speaker : Icons.speaker_outlined,
                      color: selected ? scheme.primary : scheme.onSurfaceVariant,
                    ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          room.name,
                          style: Theme.of(context).textTheme.titleSmall
                              ?.copyWith(fontWeight: selected ? FontWeight.w600 : FontWeight.w500),
                        ),
                        if (nowLine.isNotEmpty)
                          Text(
                            nowLine,
                            key: Key('room-now-${room.id}'),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                          ),
                      ],
                    ),
                  ),
                  Text(
                    [
                      if (room.volume != null) '${room.volume}',
                      if (room.isGrouped) room.groupName ?? 'Grouped',
                      if (room.relayingFor != null) 'Playing for ${room.relayingFor}',
                    ].join(' · '),
                    key: Key('room-status-${room.id}'),
                    style: Theme.of(context).textTheme.labelSmall?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                  IconButton(
                    key: Key('room-group-${room.id}'),
                    tooltip: 'Group with other rooms',
                    icon: Icon(room.isGrouped ? Icons.link : Icons.add_link, size: 20),
                    onPressed: () => showGroupSheet(context, room),
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
                onChanged: (value) => ref.read(playbackProvider.notifier).setVolume(room.id, value.round()),
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
              const SliverFillRemaining(hasScrollBody: true, child: RoomsPanel()),
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

/// Title for the empty rooms list: setup, still starting, or waiting for rooms.
String emptyRoomsTitle(PlaybackState state) {
  if (state.discovering) {
    return 'Looking for players…';
  }
  if (state.mockMode) {
    return 'No rooms yet';
  }
  if (state.connected) {
    return state.systemStarting ? 'Your system is still starting…' : 'Waiting for your rooms…';
  }
  return 'Set up your first player';
}

String emptyRoomsDetail(PlaybackState state) {
  if (state.systemStarting) {
    final detail = state.maDetail;
    return (detail == null || detail.isEmpty) ? 'Your player is getting ready.' : detail;
  }
  return 'Your players appear here as soon as they are on the network.';
}

/// Choose which rooms play together with [room]. [room]'s group leads.
Future<void> showGroupSheet(BuildContext context, Room room) {
  return showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (_) => _GroupSheet(room: room),
  );
}

class _GroupSheet extends ConsumerStatefulWidget {
  const _GroupSheet({required this.room});

  final Room room;

  @override
  ConsumerState<_GroupSheet> createState() => _GroupSheetState();
}

class _GroupSheetState extends ConsumerState<_GroupSheet> {
  late Set<String> _selected;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _selected = {widget.room.id, ...widget.room.memberIds};
  }

  Future<void> _apply() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final store = ref.read(playbackProvider.notifier);
    try {
      if (_selected.length <= 1 && widget.room.isGrouped) {
        await store.leaveGroup(widget.room.id);
      } else {
        await store.setGroupMembers(widget.room.id, _selected.toList());
      }
      if (mounted) {
        Navigator.of(context).pop();
      }
    } on Object catch (err) {
      setState(() => _error = err.toString());
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final rooms = ref.watch(playbackProvider).rooms;
    final text = Theme.of(context).textTheme;
    final others = rooms.where((r) => r.id != widget.room.id).toList();
    final count = _selected.length;
    final title = count > 1 ? '${widget.room.name} + ${count - 1}' : widget.room.name;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Play together', style: text.titleLarge),
            const SizedBox(height: 4),
            Text(
              'Spotify and AirPlay show this group as "$title".',
              key: const Key('group-sheet-name'),
              style: text.bodySmall,
            ),
            const SizedBox(height: 8),
            if (others.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 16),
                child: Text('Add a second player to group rooms.'),
              ),
            for (final other in others)
              CheckboxListTile(
                key: Key('group-member-${other.id}'),
                contentPadding: EdgeInsets.zero,
                value: _selected.contains(other.id),
                title: Text(other.name),
                subtitle: other.isGrouped && !widget.room.memberIds.contains(other.id)
                    ? Text('Now in ${other.groupName ?? 'another group'}')
                    : null,
                onChanged: _busy
                    ? null
                    : (v) => setState(() {
                        if (v == true) {
                          _selected.add(other.id);
                        } else {
                          _selected.remove(other.id);
                        }
                      }),
              ),
            if (widget.room.isGrouped)
              TextButton(
                key: const Key('group-leave'),
                onPressed: _busy
                    ? null
                    : () async {
                        setState(() => _selected = {widget.room.id});
                        await _apply();
                      },
                child: Text('Take ${widget.room.name} out of the group'),
              ),
            if (_error != null) Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            const SizedBox(height: 8),
            FilledButton(
              key: const Key('group-apply'),
              onPressed: _busy ? null : _apply,
              child: _busy
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Text('Done'),
            ),
          ],
        ),
      ),
    );
  }
}
