import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../spotify_store.dart';

/// One-time Spotify approval for a speaker (0.6.19): open spotify.com/pair
/// (the link usually carries the code) or type the code there. Closes with
/// true once the speaker is signed in; it stays signed in afterwards.
Future<bool> showSpotifyPairDialog(BuildContext context, SpotifyPairingRequired need) async {
  final ok = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (_) => _PairDialog(need: need),
  );
  return ok ?? false;
}

class _PairDialog extends ConsumerStatefulWidget {
  const _PairDialog({required this.need});

  final SpotifyPairingRequired need;

  @override
  ConsumerState<_PairDialog> createState() => _PairDialogState();
}

class _PairDialogState extends ConsumerState<_PairDialog> {
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    unawaited(_wait());
  }

  Future<void> _wait() async {
    final ok = await ref.read(spotifyProvider.notifier).waitForSpeaker(widget.need.targetId);
    if (!mounted) {
      return;
    }
    if (ok) {
      Navigator.of(context).pop(true);
    } else {
      setState(() => _failed = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final pairing = widget.need.pairing;
    final speaker = widget.need.speakerName ?? 'this speaker';
    return AlertDialog(
      key: const Key('spotify-pair-dialog'),
      title: Text('Connect $speaker to Spotify'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Spotify asks you to approve each speaker once. Open the link, '
            'sign in if asked, and confirm. If it asks for a code, enter:',
          ),
          const SizedBox(height: 12),
          Center(
            child: SelectableText(
              pairing.code,
              key: const Key('spotify-pair-code'),
              style: text.headlineMedium?.copyWith(fontWeight: FontWeight.w700, letterSpacing: 2),
            ),
          ),
          const SizedBox(height: 12),
          if (_failed)
            Text(
              'The code expired or was declined. Close this and play again for a new code.',
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            )
          else
            const Row(
              children: [
                SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                SizedBox(width: 10),
                Expanded(child: Text('Waiting for Spotify…')),
              ],
            ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () async {
            await ref.read(spotifyProvider.notifier).cancelPairing(widget.need.targetId).catchError((_) {});
            if (context.mounted) {
              Navigator.of(context).pop(false);
            }
          },
          child: Text(_failed ? 'Close' : 'Cancel'),
        ),
        TextButton(
          onPressed: () => Clipboard.setData(ClipboardData(text: pairing.code)),
          child: const Text('Copy code'),
        ),
        FilledButton(
          key: const Key('spotify-pair-open'),
          onPressed: _failed
              ? null
              : () => launchUrl(Uri.parse(pairing.url), mode: LaunchMode.externalApplication),
          child: const Text('Open spotify.com/pair'),
        ),
      ],
    );
  }
}
