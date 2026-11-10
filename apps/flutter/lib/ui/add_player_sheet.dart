import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/player_settings.dart';
import '../state/playback_store.dart';

Future<void> showAddPlayerSheet(BuildContext context) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (context) => const AddPlayerSheet(),
  );
}

/// Wizard: list unclaimed Pis, name the room, join this household.
class AddPlayerSheet extends ConsumerStatefulWidget {
  const AddPlayerSheet({super.key});

  @override
  ConsumerState<AddPlayerSheet> createState() => _AddPlayerSheetState();
}

class _AddPlayerSheetState extends ConsumerState<AddPlayerSheet> {
  final _name = TextEditingController();
  List<UnclaimedPlayer> _players = const [];
  String? _selectedId;
  var _loading = true;
  var _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final players = await ref.read(playbackProvider.notifier).listUnclaimed();
      if (!mounted) {
        return;
      }
      setState(() {
        _players = players;
        _loading = false;
        if (_selectedId != null && players.every((p) => p.id != _selectedId)) {
          _selectedId = null;
        }
      });
    } on Object catch (err) {
      if (!mounted) {
        return;
      }
      setState(() {
        _loading = false;
        _error = err.toString();
      });
    }
  }

  Future<void> _adopt() async {
    final selectedId = _selectedId;
    final name = _name.text.trim();
    if (selectedId == null || name.isEmpty) {
      setState(() => _error = 'Choose a player and give the room a name.');
      return;
    }
    final selected = _players.firstWhere((player) => player.id == selectedId);
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await ref.read(playbackProvider.notifier).adoptPlayer(name: name, id: selected.id, url: selected.url);
      if (mounted) {
        Navigator.of(context).pop();
      }
    } on Object catch (err) {
      if (!mounted) {
        return;
      }
      setState(() {
        _saving = false;
        _error = err.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final padding = MediaQuery.viewInsetsOf(context);
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 0, 20, 20 + padding.bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('Add a player', style: text.titleLarge?.copyWith(fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text(
            'New Pis show up here after they boot on this Wi-Fi. Name the room and they join this system.',
            style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
          ),
          const SizedBox(height: 16),
          if (_loading)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 24),
              child: Center(child: CircularProgressIndicator()),
            )
          else if (_players.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: Text(
                'No new players found. Check the Pi is on the same network, then try again.',
                style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
              ),
            )
          else
            for (final player in _players)
              RadioListTile<String>(
                key: Key('unclaimed-${player.id}'),
                value: player.id,
                groupValue: _selectedId,
                onChanged: _saving
                    ? null
                    : (value) {
                        setState(() {
                          _selectedId = value;
                          if (_name.text.isEmpty) {
                            _name.text = _suggestedName(player.name);
                          }
                        });
                      },
                title: Text(player.name),
                subtitle: Text(player.address ?? player.url),
              ),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: _loading || _saving ? null : _refresh,
              icon: const Icon(Icons.refresh),
              label: const Text('Search again'),
            ),
          ),
          const SizedBox(height: 8),
          TextField(
            key: const Key('new-room-name'),
            controller: _name,
            enabled: !_saving,
            textCapitalization: TextCapitalization.words,
            decoration: const InputDecoration(
              labelText: 'Room name',
              hintText: 'Kitchen',
              border: OutlineInputBorder(),
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!, style: text.bodySmall?.copyWith(color: scheme.error)),
          ],
          const SizedBox(height: 16),
          FilledButton(
            key: const Key('join-household'),
            onPressed: _saving ? null : _adopt,
            child: _saving
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Text('Join this system'),
          ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}

String _suggestedName(String advertised) {
  const prefix = 'freesounder-';
  if (advertised.toLowerCase().startsWith(prefix) && advertised.length > prefix.length) {
    final rest = advertised.substring(prefix.length);
    return rest[0].toUpperCase() + rest.substring(1);
  }
  return advertised;
}
