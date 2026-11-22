import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/connection_prefs.dart';
import '../app_version.dart';
import '../spotify/spotify_store.dart';
import '../spotify/ui/spotify_brand.dart';
import '../spotify/ui/spotify_screen.dart';
import '../state/playback_store.dart';
import 'add_player_sheet.dart';
import 'radio_screen.dart';

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
    if (_versionTaps >= 7) {
      _versionTaps = 0;
      ref.read(developerModeProvider.notifier).toggle();
      final on = ref.read(developerModeProvider);
      ScaffoldMessenger.maybeOf(context)?.showSnackBar(
        SnackBar(content: Text(on ? 'Developer mode on' : 'Developer mode off')),
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
          _StatusCard(state: state, developer: ref.watch(developerModeProvider)),
          if (state.error != null) ...[
            const SizedBox(height: 8),
            Text(state.error!, style: text.bodySmall?.copyWith(color: scheme.error)),
          ],
          // 0.6.19: picking a player by address is a developer tool now; it
          // also shows in demo mode and when the app cannot reach the home.
          if (ref.watch(developerModeProvider) || !state.connected || state.mockMode) ...[
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
                  color: host.url == state.hostUrl && !state.mockMode ? scheme.primary : scheme.onSurfaceVariant,
                ),
                title: Text(host.name),
                subtitle: ref.watch(developerModeProvider) ? Text(host.url) : null,
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
                onPressed: _busy ? null : () => _run(() => ref.read(playbackProvider.notifier).connectTo(_host.text)),
                child: const Text('Connect'),
              ),
              OutlinedButton(
                onPressed: _busy ? null : () => _run(() => ref.read(playbackProvider.notifier).reconnect()),
                child: const Text('Reconnect'),
              ),
              OutlinedButton(
                onPressed: _busy ? null : () => _run(() => ref.read(playbackProvider.notifier).refreshDiscovery()),
                child: const Text('Search again'),
              ),
            ],
          ),
          ],
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
          ListTile(
            key: const Key('reset-help-tile'),
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.restart_alt),
            title: const Text('Reset a player'),
            subtitle: const Text('Start a player over from scratch (Wi-Fi, home and room name)'),
            onTap: () => showResetHelp(context),
          ),
          const SizedBox(height: 16),
          Text('Music', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          ListTile(
            key: const Key('radio-tile'),
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.radio_outlined),
            title: const Text('Radio'),
            subtitle: const Text('Search Radio Browser and play a station in the selected room'),
            onTap: () => showRadioScreen(context),
          ),
          Consumer(
            builder: (context, ref, _) {
              final spotify = ref.watch(spotifyProvider);
              return ListTile(
                key: const Key('spotify-tile'),
                contentPadding: EdgeInsets.zero,
                leading: spotify.signedIn
                    ? const SpotifyMark.icon(key: Key('spotify-tile-mark'))
                    : const Icon(Icons.library_music_outlined),
                title: const Text('Spotify'),
                subtitle: Text(
                  spotify.signedIn
                      ? 'Connected${spotify.displayName?.isNotEmpty == true ? ' as ${spotify.displayName}' : ''}. Browse and play in any room.'
                      : 'Connect your Spotify Premium account to browse and play here',
                ),
                trailing: spotify.signedIn
                    ? TextButton(
                        key: const Key('spotify-sign-out'),
                        onPressed: () => ref.read(spotifyProvider.notifier).signOut(),
                        child: const Text('Sign out'),
                      )
                    : null,
                onTap: () => showSpotifyScreen(context),
              );
            },
          ),
          const ListTile(
            key: Key('streaming-hint-tile'),
            contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.cast_outlined),
            title: Text('Spotify app and AirPlay'),
            subtitle: Text(
              'Every room or group shows up in the Spotify app (Spotify Connect) and in '
              'AirPlay as one speaker, for example "Kitchen + 1". Pick it there.',
            ),
          ),
          if (_busy) ...[const SizedBox(height: 16), const LinearProgressIndicator()],
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
  const _StatusCard({required this.state, this.developer = false});

  final PlaybackState state;
  final bool developer;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final title = state.mockMode ? 'Demo household' : (state.connected ? 'Connected' : 'Not connected');
    final rooms = state.rooms.where((r) => !r.isGroup).length;
    // The player's address is only for developers; everyone else sees the home.
    final detail = state.mockMode
        ? 'Three example rooms. Connect to a player when you have a Pi ready.'
        : developer
        ? 'Connected to ${state.hostUrl ?? 'no player yet'}'
        : state.connected
        ? (rooms == 1 ? '1 room in your home' : '$rooms rooms in your home')
        : (state.hostUrl == null ? 'No player set up yet' : 'Looking for your players on this Wi-Fi…');
    return Card(
      elevation: 0,
      color: scheme.surfaceContainerLow,
      child: ListTile(
        leading: Icon(state.mockMode ? Icons.science_outlined : Icons.home_outlined, color: scheme.primary),
        title: Text(title),
        subtitle: Text(detail),
      ),
    );
  }
}

/// How to factory-reset a player without the app (docs/reset.md).
const resetHelpText =
    'A reset forgets the player\'s Wi-Fi, its home and its room name, and puts it back into '
    'Bluetooth setup mode, ready to add again.\n\n'
    'In the app: open the room, Room settings, then Factory reset at the bottom.\n\n'
    'Raspberry Pi 5: press its power button 5 times quickly (within 3 seconds). '
    'One press shuts it down cleanly.\n\n'
    'Any Pi, no tools: plug it in, wait about 20 seconds, unplug. Do this 5 times in a row '
    '(from the 2nd time the green light blinks once for each), then plug it in and leave it.\n\n'
    'The screen on the Pi (HDMI) then shows "Reset - ready for setup". '
    'Remove the old room from your other players with the bin icon next to it.';

Future<void> showResetHelp(BuildContext context) {
  return showDialog<void>(
    context: context,
    builder: (dialog) => AlertDialog(
      title: const Text('Reset a player'),
      content: const SingleChildScrollView(child: Text(resetHelpText, key: Key('reset-help-text'))),
      actions: [TextButton(onPressed: () => Navigator.of(dialog).pop(), child: const Text('OK'))],
    ),
  );
}
