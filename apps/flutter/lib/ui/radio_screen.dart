import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/errors.dart';
import '../models/radio_station.dart';
import '../state/playback_store.dart';

Future<void> showRadioScreen(BuildContext context) {
  return Navigator.of(context).push(
    MaterialPageRoute<void>(builder: (_) => const RadioScreen()),
  );
}

/// Radio Browser search. The selected room's player (its group leader) plays
/// the stream itself, so the phone can leave afterwards.
class RadioScreen extends ConsumerStatefulWidget {
  const RadioScreen({super.key});

  @override
  ConsumerState<RadioScreen> createState() => _RadioScreenState();
}

class _RadioScreenState extends ConsumerState<RadioScreen> {
  final _query = TextEditingController();
  Timer? _debounce;
  List<RadioStation> _results = const [];
  List<RadioStation> _recent = const [];
  bool _loading = false;
  String? _error;
  String? _playing;

  @override
  void initState() {
    super.initState();
    _search('');
    ref.read(playbackProvider.notifier).recentRadio().then((items) {
      if (mounted) {
        setState(() => _recent = items);
      }
    });
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _query.dispose();
    super.dispose();
  }

  void _onChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), () => _search(value.trim()));
  }

  Future<void> _search(String query) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await ref.read(playbackProvider.notifier).searchRadio(query);
      if (mounted) {
        setState(() => _results = results);
      }
    } on Object catch (err) {
      if (mounted) {
        setState(() => _error = userMessageFor(err));
      }
    } finally {
      if (mounted) {
        setState(() => _loading = false);
      }
    }
  }

  Future<void> _play(RadioStation station) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _playing = station.stationuuid ?? station.url);
    try {
      await ref.read(playbackProvider.notifier).playRadio(station);
      final room = ref.read(playbackProvider).activeRoom;
      messenger.showSnackBar(
        SnackBar(content: Text('Playing ${station.name} in ${room?.groupName ?? room?.name ?? 'the room'}')),
      );
    } on Object catch (err) {
      messenger.showSnackBar(SnackBar(content: Text(userMessageFor(err))));
    } finally {
      if (mounted) {
        setState(() => _playing = null);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(playbackProvider);
    final room = state.activeRoom;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final showRecent = _query.text.trim().isEmpty && _recent.isNotEmpty;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Radio'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(28),
          child: Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(
              room == null ? 'No room selected' : 'Plays in ${room.groupName ?? room.name}',
              key: const Key('radio-target-room'),
              style: text.labelMedium?.copyWith(color: scheme.onSurfaceVariant),
            ),
          ),
        ),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
            child: TextField(
              key: const Key('radio-search'),
              controller: _query,
              onChanged: _onChanged,
              onSubmitted: (v) => _search(v.trim()),
              textInputAction: TextInputAction.search,
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                hintText: 'Search stations',
                border: OutlineInputBorder(),
              ),
            ),
          ),
          if (_loading) const LinearProgressIndicator(),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(_error!, style: TextStyle(color: scheme.error)),
            ),
          Expanded(
            child: ListView(
              key: const Key('radio-results'),
              children: [
                if (showRecent) ...[
                  _header(context, 'Recently played'),
                  for (final station in _recent) _tile(station),
                  _header(context, 'Popular near you'),
                ],
                for (final station in _results) _tile(station),
                if (!_loading && _results.isEmpty && _error == null)
                  const Padding(
                    padding: EdgeInsets.all(24),
                    child: Text('No stations found.', textAlign: TextAlign.center),
                  ),
                const Padding(
                  padding: EdgeInsets.all(16),
                  child: Text(
                    'Stations from radio-browser.info, a free community directory.',
                    textAlign: TextAlign.center,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _header(BuildContext context, String title) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
      child: Text(title, style: Theme.of(context).textTheme.titleSmall),
    );
  }

  Widget _tile(RadioStation station) {
    final id = station.stationuuid ?? station.url;
    final busy = _playing != null && _playing == id;
    return ListTile(
      key: Key('radio-station-${station.stationuuid ?? station.name}'),
      leading: _StationIcon(url: station.favicon),
      title: Text(station.name, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: station.subtitle.isEmpty
          ? null
          : Text(station.subtitle, maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: busy
          ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
          : const Icon(Icons.play_arrow),
      onTap: busy ? null : () => _play(station),
    );
  }
}

class _StationIcon extends StatelessWidget {
  const _StationIcon({this.url});

  final String? url;

  @override
  Widget build(BuildContext context) {
    final fallback = CircleAvatar(
      backgroundColor: Theme.of(context).colorScheme.secondaryContainer,
      child: const Icon(Icons.radio),
    );
    final src = url;
    if (src == null || !src.startsWith('http')) {
      return fallback;
    }
    return ClipOval(
      child: Image.network(
        src,
        width: 40,
        height: 40,
        fit: BoxFit.cover,
        errorBuilder: (_, _, _) => fallback,
      ),
    );
  }
}
