import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/connection_prefs.dart';
import '../app_version.dart';
import '../state/playback_store.dart';
import 'add_player_sheet.dart';
import 'music_services_screen.dart';

/// Household connection: discovered systems, manual address, demo switch.
class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  late final TextEditingController _host;
  var _busy = false;
  var _versionTaps = 0;

  @override
  void initState() {
    super.initState();
    final state = ref.read(playbackProvider);
    _host = TextEditingController(text: state.hostUrl ?? '');
    Future.microtask(() => ref.read(playbackProvider.notifier).refreshDiscovery());
  }

  @override
  void dispose() {
    _host.dispose();
    super.dispose();
  }

  void _onVersionTap() {
    _versionTaps += 1;
    if (_versionTaps == 7) {
      ref.read(developerModeProvider.notifier).set(true);
      ScaffoldMessenger.maybeOf(context)?.showSnackBar(
        const SnackBar(content: Text('Developer mode on')),
      );
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(playbackProvider);
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
        children: [
          Text('System', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          _StatusCard(state: state),
          if (state.error != null) ...[
            const SizedBox(height: 8),
            Text(state.error!, style: text.bodySmall?.copyWith(color: scheme.error)),
          ],
          const SizedBox(height: 24),
          Text('Connect', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text(
            'The app looks for a FreeSounder host on your Wi-Fi. You can also type its address.',
            style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
          ),
          const SizedBox(height: 12),
          if (state.discoveredHosts.isEmpty)
            Text(
              'No systems found on this network yet.',
              style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
            )
          else
            for (final host in state.discoveredHosts)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(
                  Icons.speaker_group_outlined,
                  color: host.url == state.hostUrl && !state.mockMode
                      ? scheme.primary
                      : scheme.onSurfaceVariant,
                ),
                title: Text(host.name),
                subtitle: Text(host.url),
                trailing: host.url == state.hostUrl && !state.mockMode
                    ? Text('Connected', style: text.labelSmall?.copyWith(color: scheme.primary))
                    : null,
                onTap: _busy ? null : () => _run(() => ref.read(playbackProvider.notifier).connectTo(host.url)),
              ),
          const SizedBox(height: 8),
          TextField(
            key: const Key('host-address-field'),
            controller: _host,
            enabled: !_busy,
            decoration: const InputDecoration(
              labelText: 'Host address',
              hintText: '192.168.1.10 or http://freesounder.local:8080',
              border: OutlineInputBorder(),
            ),
            keyboardType: TextInputType.url,
            textInputAction: TextInputAction.go,
            onSubmitted: (value) => _run(() => ref.read(playbackProvider.notifier).connectTo(value)),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FilledButton(
                onPressed: _busy
                    ? null
                    : () => _run(() => ref.read(playbackProvider.notifier).connectTo(_host.text)),
                child: const Text('Connect'),
              ),
              OutlinedButton(
                onPressed: _busy ? null : () => _run(() => ref.read(playbackProvider.notifier).reconnect()),
                child: const Text('Reconnect'),
              ),
              OutlinedButton(
                onPressed: _busy
                    ? null
                    : () => _run(() => ref.read(playbackProvider.notifier).refreshDiscovery()),
                child: const Text('Search again'),
              ),
            ],
          ),
          const SizedBox(height: 28),
          Text('Demo', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text(
            'Demo mode lets you try the app without a Pi. It stays off unless you turn it on, and then it is remembered on this device.',
            style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
          ),
          SwitchListTile(
            key: const Key('demo-switch'),
            contentPadding: EdgeInsets.zero,
            title: const Text('Use demo household'),
            value: state.mockMode,
            onChanged: _busy
                ? null
                : (value) {
                    if (value) {
                      _run(() => ref.read(playbackProvider.notifier).useDemo());
                    } else {
                      _run(() => ref.read(playbackProvider.notifier).leaveDemoAndDiscover());
                    }
                  },
          ),
          const SizedBox(height: 16),
          Text('Players', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          ListTile(
            key: const Key('add-player-tile'),
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.add_circle_outline),
            title: const Text('Add a player'),
            subtitle: const Text('Set up a new Pi over Bluetooth, or adopt one already on your network'),
            onTap: () => showAddPlayerSheet(context),
          ),
          const SizedBox(height: 16),
          Text('Music', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          ListTile(
            key: const Key('music-services-tile'),
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.library_music_outlined),
            title: const Text('Music services'),
            subtitle: const Text('Add Spotify, Tidal and other sources without leaving the app'),
            onTap: () {
              Navigator.of(context).push(
                MaterialPageRoute<void>(builder: (_) => const MusicServicesScreen()),
              );
            },
          ),
          if (_busy) ...[
            const SizedBox(height: 16),
            const LinearProgressIndicator(),
          ],
          const SizedBox(height: 16),
          ListTile(
            key: const Key('app-version-tile'),
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.info_outline),
            title: Text('FreeSounder $appVersion'),
            subtitle: ref.watch(developerModeProvider)
                ? const Text('Developer mode on (Bluetooth details shown during setup)')
                : null,
            onTap: _onVersionTap,
          ),
        ],
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({required this.state});

  final PlaybackState state;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final title = state.mockMode
        ? 'Demo household'
        : (state.connected ? 'Connected' : 'Not connected');
    final detail = state.mockMode
        ? 'Three example rooms. Connect to a host when you have a Pi ready.'
        : (state.hostUrl ?? 'No host saved yet');
    return Card(
      elevation: 0,
      color: scheme.surfaceContainerLow,
      child: ListTile(
        leading: Icon(
          state.mockMode ? Icons.science_outlined : Icons.home_outlined,
          color: scheme.primary,
        ),
        title: Text(title),
        subtitle: Text(detail),
      ),
    );
  }
}
