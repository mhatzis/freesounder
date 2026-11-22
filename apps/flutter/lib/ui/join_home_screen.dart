import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/errors.dart';
import '../state/join_home.dart';
import '../state/playback_store.dart';

/// "Join the <name> home" for a phone new to this home (0.6.19).
class JoinHomeScreen extends ConsumerStatefulWidget {
  const JoinHomeScreen({super.key, required this.prompt});

  final JoinPrompt prompt;

  @override
  ConsumerState<JoinHomeScreen> createState() => _JoinHomeScreenState();
}

class _JoinHomeScreenState extends ConsumerState<JoinHomeScreen> {
  final _code = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _code.dispose();
    super.dispose();
  }

  Future<void> _join() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(playbackProvider.notifier).joinHome(_code.text);
    } on Object catch (err) {
      if (mounted) {
        setState(() => _error = userMessageFor(err));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      key: const Key('join-home-screen'),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 420),
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Icon(Icons.home_outlined, size: 56, color: scheme.primary),
                  const SizedBox(height: 16),
                  Text(
                    'Join the ${widget.prompt.name} home',
                    textAlign: TextAlign.center,
                    style: text.headlineSmall?.copyWith(fontWeight: FontWeight.w600),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'FreeSounder found this home on your Wi-Fi. Enter its 6-digit code. '
                    'Anyone already using it can find the code in Settings.',
                    textAlign: TextAlign.center,
                    style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                  const SizedBox(height: 24),
                  TextField(
                    key: const Key('join-home-code'),
                    controller: _code,
                    enabled: !_busy,
                    autofocus: true,
                    keyboardType: TextInputType.number,
                    inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9 ]')), LengthLimitingTextInputFormatter(7)],
                    textAlign: TextAlign.center,
                    style: text.headlineMedium?.copyWith(letterSpacing: 6),
                    decoration: const InputDecoration(hintText: '000 000', border: OutlineInputBorder()),
                    onSubmitted: (_) => _join(),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 8),
                    Text(_error!, key: const Key('join-home-error'), style: TextStyle(color: scheme.error)),
                  ],
                  const SizedBox(height: 16),
                  FilledButton(
                    key: const Key('join-home-button'),
                    onPressed: _busy ? null : _join,
                    child: _busy
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Text('Join'),
                  ),
                  const SizedBox(height: 8),
                  TextButton(
                    onPressed: _busy
                        ? null
                        : () {
                            ref.read(joinHomeProvider.notifier).clear();
                            unawaited(ref.read(playbackProvider.notifier).useDemo());
                          },
                    child: const Text('Not now (try the demo instead)'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
