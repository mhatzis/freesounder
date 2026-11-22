import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/room.dart';
import '../state/playback_store.dart';

/// Rooms that play together with [room], [room] first.
List<Room> groupRooms(List<Room> rooms, Room room) {
  if (!room.isGrouped) {
    return [room];
  }
  final ids = room.memberIds.toSet();
  return [room, ...rooms.where((r) => r.id != room.id && ids.contains(r.id))];
}

/// The group's volume, as the player computes it: the rooms' average.
int groupLevel(List<Room> members) {
  if (members.isEmpty) {
    return 0;
  }
  final total = members.fold<int>(0, (sum, r) => sum + (r.volume ?? 0));
  return (total / members.length).round();
}

IconData volumeIcon(int level, {bool muted = false}) {
  if (muted || level == 0) {
    return Icons.volume_off;
  }
  return level < 40 ? Icons.volume_down : Icons.volume_up;
}

/// Volume for [roomId]: one slider for a single room; for a group, an
/// "All rooms" slider (moves every room by the same amount) and a slider
/// and mute button for each room.
Future<void> showVolumeSheet(BuildContext context, String roomId) {
  return showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (_) => VolumeSheet(roomId: roomId),
  );
}

class VolumeSheet extends ConsumerStatefulWidget {
  const VolumeSheet({super.key, required this.roomId});

  final String roomId;

  @override
  ConsumerState<VolumeSheet> createState() => _VolumeSheetState();
}

class _VolumeSheetState extends ConsumerState<VolumeSheet> {
  /// While the "All rooms" slider moves: where it is, and each room's level
  /// when the drag started (each room shows its own level + the change).
  double? _master;
  Map<String, int>? _base;
  int? _baseLevel;
  final Map<String, double> _dragging = {};

  int _shown(Room r) {
    final own = _dragging[r.id];
    if (own != null) {
      return own.round();
    }
    final master = _master;
    final base = _base;
    if (master != null && base != null && _baseLevel != null) {
      return ((base[r.id] ?? 0) + master.round() - _baseLevel!).clamp(0, 100);
    }
    return r.volume ?? 0;
  }

  @override
  Widget build(BuildContext context) {
    final rooms = ref.watch(playbackProvider).rooms;
    final room = rooms.where((r) => r.id == widget.roomId).firstOrNull;
    if (room == null) {
      return const SizedBox(height: 120, child: Center(child: Text('This room is not available.')));
    }
    final members = groupRooms(rooms, room);
    final store = ref.read(playbackProvider.notifier);
    final text = Theme.of(context).textTheme;
    final grouped = members.length > 1;
    final allMuted = members.every((r) => r.muted == true);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Volume', style: text.titleLarge),
            if (grouped)
              Text(room.groupName ?? members.map((r) => r.name).join(' + '), style: text.bodySmall),
            const SizedBox(height: 8),
            if (grouped) ...[
              VolumeRow(
                key: const Key('volume-group'),
                label: 'All rooms',
                emphasis: true,
                level: (_master ?? groupLevel(members).toDouble()).round(),
                muted: allMuted,
                onMute: () => unawaited(_muteAll(members, !allMuted)),
                onChanged: (v) {
                  setState(() {
                    _base ??= {for (final r in members) r.id: r.volume ?? 0};
                    _baseLevel ??= groupLevel(members);
                    _master = v;
                  });
                  // 0.6.19: each room is set directly (as smooth as a room's
                  // own slider), not through group commands.
                  for (final r in members) {
                    store.dragVolume(r.id, _shown(r).toDouble());
                  }
                },
                onChangeEnd: (v) async {
                  setState(() => _master = v);
                  try {
                    await Future.wait([for (final r in members) store.commitVolume(r.id, _shown(r).toDouble())]);
                  } on Object {
                    // The rooms show the player's levels again below.
                  }
                  if (mounted) {
                    setState(() {
                      _master = null;
                      _base = null;
                      _baseLevel = null;
                    });
                  }
                },
              ),
              const Divider(height: 16),
            ],
            Flexible(
              child: ListView(
                shrinkWrap: true,
                children: [
                  for (final r in members)
                    VolumeRow(
                      key: Key('volume-room-${r.id}'),
                      label: r.name,
                      level: _shown(r),
                      muted: r.muted == true,
                      enabled: r.available,
                      onMute: () => unawaited(store.setMuted(r.id, !(r.muted == true)).catchError((_) {})),
                      onChanged: (v) {
                        setState(() => _dragging[r.id] = v);
                        store.dragVolume(r.id, v);
                      },
                      onChangeEnd: (v) async {
                        try {
                          await store.commitVolume(r.id, v);
                        } on Object {
                          // Shown again from the player's level.
                        }
                        if (mounted) {
                          setState(() => _dragging.remove(r.id));
                        }
                      },
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _muteAll(List<Room> members, bool muted) async {
    final store = ref.read(playbackProvider.notifier);
    await Future.wait([for (final r in members) store.setMuted(r.id, muted).catchError((_) {})]);
  }
}

/// One labelled volume slider with a mute button and the level in numbers.
class VolumeRow extends StatelessWidget {
  const VolumeRow({
    super.key,
    required this.label,
    required this.level,
    required this.onChanged,
    required this.onChangeEnd,
    this.muted = false,
    this.onMute,
    this.enabled = true,
    this.emphasis = false,
  });

  final String label;
  final int level;
  final bool muted;
  final bool enabled;
  final bool emphasis;
  final VoidCallback? onMute;
  final ValueChanged<double> onChanged;
  final ValueChanged<double> onChangeEnd;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Semantics(
      label: '$label volume',
      value: muted ? 'muted' : '$level percent',
      child: Row(
        children: [
          IconButton(
            tooltip: muted ? 'Unmute $label' : 'Mute $label',
            onPressed: enabled ? onMute : null,
            icon: Icon(volumeIcon(level, muted: muted)),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.only(left: 24),
                  child: Text(
                    label,
                    style: emphasis ? text.titleSmall : text.bodyMedium,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                Slider(
                  value: level.clamp(0, 100).toDouble(),
                  max: 100,
                  onChanged: enabled ? onChanged : null,
                  onChangeEnd: enabled ? onChangeEnd : null,
                ),
              ],
            ),
          ),
          SizedBox(
            width: 36,
            child: Text(muted ? '–' : '$level', textAlign: TextAlign.end, style: text.labelLarge),
          ),
        ],
      ),
    );
  }
}

/// Volume right on the home screen. A single room gets a slider; a group
/// gets its level as a bar that opens the volume panel (all rooms and each
/// room), like a group's volume in Sonos.
class QuickVolume extends ConsumerStatefulWidget {
  const QuickVolume({super.key, required this.roomId});

  final String roomId;

  @override
  ConsumerState<QuickVolume> createState() => _QuickVolumeState();
}

class _QuickVolumeState extends ConsumerState<QuickVolume> {
  double? _dragging;

  @override
  Widget build(BuildContext context) {
    final rooms = ref.watch(playbackProvider).rooms;
    final room = rooms.where((r) => r.id == widget.roomId).firstOrNull;
    if (room == null) {
      return const SizedBox.shrink();
    }
    final scheme = Theme.of(context).colorScheme;
    final store = ref.read(playbackProvider.notifier);
    final members = groupRooms(rooms, room);
    if (members.length > 1) {
      final level = groupLevel(members);
      return Semantics(
        button: true,
        label: 'Group volume $level percent. Opens the volume of each room.',
        child: InkWell(
          key: const Key('quick-volume-group'),
          borderRadius: BorderRadius.circular(99),
          onTap: () => showVolumeSheet(context, room.id),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
            child: Row(
              children: [
                Icon(volumeIcon(level), size: 22),
                const SizedBox(width: 12),
                Expanded(
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(99),
                    child: LinearProgressIndicator(
                      value: level / 100,
                      minHeight: 6,
                      backgroundColor: scheme.surfaceContainerHighest,
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Text('$level', style: Theme.of(context).textTheme.labelLarge),
                const SizedBox(width: 4),
                Icon(Icons.unfold_more, size: 18, color: scheme.onSurfaceVariant),
              ],
            ),
          ),
        ),
      );
    }
    final level = (_dragging ?? (room.volume ?? 0).toDouble()).round();
    return Row(
      children: [
        IconButton(
          tooltip: room.muted == true ? 'Unmute' : 'Mute',
          onPressed: () => unawaited(store.setMuted(room.id, !(room.muted == true)).catchError((_) {})),
          icon: Icon(volumeIcon(level, muted: room.muted == true)),
        ),
        Expanded(
          child: Slider(
            key: const Key('quick-volume'),
            value: level.clamp(0, 100).toDouble(),
            max: 100,
            label: '$level',
            onChanged: (v) {
              setState(() => _dragging = v);
              store.dragVolume(room.id, v);
            },
            onChangeEnd: (v) async {
              try {
                await store.commitVolume(room.id, v);
              } on Object {
                // Back to the player's level.
              }
              if (mounted) {
                setState(() => _dragging = null);
              }
            },
          ),
        ),
        SizedBox(width: 32, child: Text('$level', textAlign: TextAlign.end)),
      ],
    );
  }
}
