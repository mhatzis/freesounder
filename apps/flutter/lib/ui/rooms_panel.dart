import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/errors.dart';
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
                    key: const Key('rooms-list'),
                    // The last room must scroll clear of the mini player.
                    padding: EdgeInsets.fromLTRB(
                      12,
                      4,
                      12,
                      24 + (asRail ? 0 : FsTokens.miniPlayerHeight + MediaQuery.paddingOf(context).bottom),
                    ),
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

  Future<void> _confirmForget(BuildContext context, WidgetRef ref) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text('Remove ${room.name}?'),
        content: const Text(
          'This player has not been seen for a while. Remove it if you reset it or no longer use it. '
          'If it comes back online it shows up again.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(dialog).pop(false), child: const Text('Cancel')),
          FilledButton(
            key: const Key('confirm-forget-room'),
            onPressed: () => Navigator.of(dialog).pop(true),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (ok == true) {
      await ref.read(playbackProvider.notifier).forgetRoom(room.id);
    }
  }

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
    final offline = !room.available;
    final status = offline
        ? 'Offline'
        : [
            if (room.volume != null) '${room.volume}',
            if (room.isGrouped) room.groupName ?? 'Grouped',
            if (room.relayingFor != null) 'Playing for ${room.relayingFor}',
          ].join(' · ');
    return Material(
      color: selected ? scheme.primaryContainer.withValues(alpha: 0.55) : Colors.transparent,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: () => ref.read(playbackProvider.notifier).selectRoom(room.id),
        onLongPress: offline ? null : () => showRoomSettingsSheet(context, roomId: room.id, roomName: room.name),
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
                      child: SizedBox(width: 36, height: 36, child: Artwork(urls: art, trackKey: np?.trackKey ?? '${room.nowTitle}|${room.nowImageUrl}')),
                    )
                  else
                    Icon(
                      selected ? Icons.speaker : Icons.speaker_outlined,
                      color: selected ? scheme.primary : scheme.onSurfaceVariant,
                    ),
                  const SizedBox(width: 12),
                  // Title takes the free width (ellipsised, never squeezed to
                  // one letter per line); status goes on its own line under it.
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          room.name,
                          key: Key('room-name-${room.id}'),
                          maxLines: 1,
                          softWrap: false,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.titleSmall
                              ?.copyWith(fontWeight: selected ? FontWeight.w600 : FontWeight.w500),
                        ),
                        if (status.isNotEmpty)
                          Text(
                            status,
                            key: Key('room-status-${room.id}'),
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.labelSmall
                                ?.copyWith(color: offline ? scheme.error : scheme.onSurfaceVariant),
                          ),
                        if (nowLine.isNotEmpty && !offline)
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
                  if (offline)
                    IconButton(
                      key: Key('room-forget-${room.id}'),
                      tooltip: 'Remove this room',
                      icon: const Icon(Icons.delete_outline, size: 20),
                      onPressed: () => _confirmForget(context, ref),
                    ),
                  if (!offline)
                    IconButton(
                      key: Key('room-group-${room.id}'),
                      tooltip: 'Group with other rooms',
                      icon: Icon(room.isGrouped ? Icons.link : Icons.add_link, size: 20),
                      onPressed: () => showGroupSheet(context, room),
                    ),
                  if (!offline)
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
                onChanged: offline ? null : (value) => ref.read(playbackProvider.notifier).dragVolume(room.id, value),
                onChangeEnd: offline
                    ? null
                    : (value) => unawaited(ref.read(playbackProvider.notifier).commitVolume(room.id, value).catchError((_) {})),
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
      setState(() => _error = userMessageFor(err));
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
              'Spotify and AirPlay show this group as "$title". '
              'Rooms you untick stop playing; the others carry on.',
              key: const Key('group-sheet-name'),
              style: text.bodySmall,
            ),
            const SizedBox(height: 8),
            if (others.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 16),
                child: Text('Add a second player to group rooms.'),
              ),
            if (others.length > 2)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton(
                  key: const Key('group-all'),
                  onPressed: _busy
                      ? null
                      : () => setState(() {
                          final all = others.every((o) => _selected.contains(o.id));
                          _selected = all ? {widget.room.id} : {widget.room.id, ...others.map((o) => o.id)};
                        }),
                  child: Text(others.every((o) => _selected.contains(o.id)) ? 'Select none' : 'Select all rooms'),
                ),
              ),
            // Up to 10 rooms in a home: the list scrolls, Done stays on screen.
            Flexible(
              child: ListView(
                key: const Key('group-member-list'),
                shrinkWrap: true,
                children: [
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
                ],
              ),
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
