import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../spotify_auth.dart';
import '../spotify_store.dart';

/// Guided setup: create a free Spotify developer app, paste its Client ID,
/// then sign in. Shown until Spotify is connected.
class SpotifySetup extends ConsumerStatefulWidget {
  const SpotifySetup({super.key});

  @override
  ConsumerState<SpotifySetup> createState() => _SpotifySetupState();
}

class _SpotifySetupState extends ConsumerState<SpotifySetup> {
  final _clientId = TextEditingController();
  bool _showOwn = false;

  @override
  void dispose() {
    _clientId.dispose();
    super.dispose();
  }

  Future<void> _copy(String value) async {
    await Clipboard.setData(ClipboardData(text: value));
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Copied $value')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final spotify = ref.watch(spotifyProvider);
    final controller = ref.read(spotifyProvider.notifier);
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final redirect = spotify.loopback ? SpotifyConfig.loopbackRedirect : SpotifyConfig.customRedirect;

    Widget step(int n, String title, List<Widget> body) => Padding(
          padding: const EdgeInsets.only(bottom: 18),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              CircleAvatar(radius: 13, child: Text('$n', style: text.labelMedium)),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: text.titleSmall?.copyWith(fontWeight: FontWeight.w600)),
                    const SizedBox(height: 4),
                    ...body,
                  ],
                ),
              ),
            ],
          ),
        );

    return ListView(
      key: const Key('spotify-setup'),
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
      children: [
        Text('Connect Spotify', style: text.headlineSmall?.copyWith(fontWeight: FontWeight.w600)),
        const SizedBox(height: 6),
        Text(
          'Browse your Spotify library here and play it on any room. You need Spotify Premium. '
          'Spotify only lets small personal apps sign in a few people, so FreeSounder uses your own '
          'free Spotify developer app. This takes about five minutes, once.',
          style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
        ),
        const SizedBox(height: 20),
        if (!spotify.configured || spotify.ownClientId || _showOwn) ...[
          step(1, 'Create a Spotify app', [
            const Text('Open the Spotify developer dashboard, log in with your Spotify account, accept the '
                'terms and tap "Create app". Any name and description will do.'),
            TextButton.icon(
              key: const Key('spotify-open-dashboard'),
              onPressed: () => launchUrl(Uri.parse(SpotifyConfig.dashboardUrl), mode: LaunchMode.externalApplication),
              icon: const Icon(Icons.open_in_new, size: 18),
              label: const Text('Open developer.spotify.com/dashboard'),
            ),
          ]),
          step(2, 'Add this Redirect URI', [
            const Text('In the app form, paste this exactly into "Redirect URIs" and tap Add. Tick "Web API", then Save.'),
            const SizedBox(height: 6),
            Material(
              color: scheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(10),
              child: ListTile(
                key: const Key('spotify-redirect-uri'),
                dense: true,
                title: SelectableText(redirect, style: const TextStyle(fontFamily: 'monospace')),
                trailing: IconButton(
                  tooltip: 'Copy',
                  icon: const Icon(Icons.copy, size: 18),
                  onPressed: () => _copy(redirect),
                ),
              ),
            ),
            SwitchListTile(
              key: const Key('spotify-loopback'),
              contentPadding: EdgeInsets.zero,
              dense: true,
              value: spotify.loopback,
              onChanged: controller.setLoopback,
              title: const Text('Spotify said "Insecure redirect URI"'),
              subtitle: const Text('Use the 127.0.0.1 address instead (add that one in the dashboard).'),
            ),
          ]),
          step(3, 'Paste the Client ID', [
            const Text('Open your new app, tap Settings and copy the "Client ID". You never need the client secret.'),
            const SizedBox(height: 8),
            TextField(
              key: const Key('spotify-client-id'),
              controller: _clientId,
              autocorrect: false,
              decoration: InputDecoration(
                labelText: 'Client ID',
                hintText: spotify.ownClientId ? 'Saved: ${spotify.clientId}' : '32 letters and digits',
                border: const OutlineInputBorder(),
              ),
              onSubmitted: controller.saveClientId,
            ),
            const SizedBox(height: 8),
            FilledButton.tonal(
              key: const Key('spotify-save-client-id'),
              onPressed: () => controller.saveClientId(_clientId.text),
              child: const Text('Save Client ID'),
            ),
          ]),
          step(4, 'Other people in your home', [
            const Text('Each extra Spotify account must be added in the dashboard under your app, then '
                '"User Management" (name and the email of their Spotify account). Up to five people.'),
          ]),
        ],
        if (spotify.configured) ...[
          FilledButton.icon(
            key: const Key('spotify-sign-in'),
            onPressed: spotify.busy ? null : controller.signIn,
            icon: spotify.busy
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.login),
            label: const Text('Sign in to Spotify'),
          ),
          if (!spotify.ownClientId && !_showOwn) ...[
            const SizedBox(height: 8),
            TextButton(
              onPressed: () => setState(() => _showOwn = true),
              child: const Text('Use my own Spotify app instead'),
            ),
          ],
        ],
        if (spotify.error != null) ...[
          const SizedBox(height: 12),
          Text(spotify.error!, key: const Key('spotify-error'), style: TextStyle(color: scheme.error)),
        ],
      ],
    );
  }
}
