import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/connection_prefs.dart';
import '../api/errors.dart';

import '../app_version.dart';
import '../models/player_settings.dart';
import '../state/playback_store.dart';

const dacOptions = <(String, String)>[
  ('auto', 'Automatic (HDMI, USB or a detected HAT)'),
  ('none', 'HDMI / USB only'),
  ('dacplus', 'Raspberry Pi DAC+ / HiFiBerry DAC+'),
  ('digiampplus', 'Raspberry Pi DigiAMP+ / HiFiBerry DigiAMP+'),
  ('hifiberry-dac', 'HiFiBerry DAC / PCM5102A board'),
  ('iqaudio-dacplus', 'IQaudio DAC+'),
];

Future<void> showRoomSettingsSheet(BuildContext context, {required String roomId, String? roomName}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (context) => RoomSettingsSheet(roomId: roomId, roomName: roomName),
  );
}

/// Per-player name, tone, volume limit and DAC.
class RoomSettingsSheet extends ConsumerStatefulWidget {
  const RoomSettingsSheet({super.key, required this.roomId, this.roomName});

  final String roomId;
  final String? roomName;

  @override
  ConsumerState<RoomSettingsSheet> createState() => _RoomSettingsSheetState();
}

class _RoomSettingsSheetState extends ConsumerState<RoomSettingsSheet> {
  final _name = TextEditingController();
  PlayerSettings? _settings;
  var _loading = true;
  var _saving = false;
  var _developerUnlocked = false;
  var _versionTaps = 0;
  var _sshBusy = false;
  SshDeveloperStatus? _ssh;
  String? _error;
  var _toneBusy = false;
  // Live preview while a tone slider is dragged: at most one request per
  // 250 ms, latest value wins; the final value is saved on release.
  Timer? _previewTimer;
  PlayerSettings? _previewPending;
  Future<void>? _previewInFlight;
  String? _toneResult;
  List<String>? _audioLines;

  @override
  void initState() {
    super.initState();
    _name.text = widget.roomName ?? '';
    // Defer so we do not write to the playback store during initState.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        _load();
      }
    });
  }

  @override
  void dispose() {
    _previewTimer?.cancel();
    _name.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final loaded = await ref.read(playbackProvider.notifier).loadSettings(widget.roomId);
      if (!mounted) {
        return;
      }
      SshDeveloperStatus? ssh;
      try {
        ssh = await ref.read(playbackProvider.notifier).loadSsh(widget.roomId);
      } on Object {
        ssh = null;
      }
      if (!mounted) {
        return;
      }
      setState(() {
        _settings = loaded;
        _name.text = loaded.name;
        _ssh = ssh;
        _loading = false;
      });
    } on Object catch (err) {
      if (!mounted) {
        return;
      }
      setState(() {
        _loading = false;
        _error = userMessageFor(err);
        _settings = PlayerSettings(name: widget.roomName ?? widget.roomId);
      });
    }
  }

  void _preview(PlayerSettings next) {
    _previewPending = next;
    _previewTimer ??= Timer(const Duration(milliseconds: 250), _flushPreview);
  }

  Future<void> _flushPreview() async {
    _previewTimer = null;
    final next = _previewPending;
    _previewPending = null;
    if (next == null || !mounted) {
      return;
    }
    final request = ref.read(playbackProvider.notifier).saveSettings(widget.roomId, next).then<void>((_) {});
    _previewInFlight = request.catchError((Object _) {});
    await _previewInFlight;
  }

  Future<void> _persist(PlayerSettings next) async {
    _previewTimer?.cancel();
    _previewTimer = null;
    _previewPending = null;
    final inFlight = _previewInFlight;
    if (inFlight != null) {
      await inFlight;
    }
    if (!mounted) {
      return;
    }
    setState(() {
      _settings = next;
      _saving = true;
      _error = null;
    });
    try {
      final saved = await ref.read(playbackProvider.notifier).saveSettings(widget.roomId, next);
      if (!mounted) {
        return;
      }
      setState(() {
        _settings = saved;
        _saving = false;
      });
    } on Object catch (err) {
      if (!mounted) {
        return;
      }
      setState(() {
        _saving = false;
        _error = userMessageFor(err);
      });
    }
  }

  Future<void> _testTone() async {
    setState(() {
      _toneBusy = true;
      _toneResult = null;
    });
    final store = ref.read(playbackProvider.notifier);
    String result;
    try {
      result = await store.testTone(widget.roomId);
    } on Object catch (err) {
      result = 'Test tone failed: ${userMessageFor(err)}';
    }
    List<String>? lines;
    try {
      lines = describeRoomAudio(await store.roomAudio(widget.roomId));
    } on Object {
      lines = null;
    }
    if (!mounted) {
      return;
    }
    setState(() {
      _toneBusy = false;
      _toneResult = result;
      _audioLines = lines;
    });
  }

  void _onVersionTap() {
    setState(() {
      _versionTaps += 1;
      if (_versionTaps >= 7) {
        _developerUnlocked = true;
      }
    });
    if (_developerUnlocked) {
      ref.read(developerModeProvider.notifier).set(true);
    }
  }

  Future<void> _toggleSsh(bool enabled) async {
    setState(() => _sshBusy = true);
    try {
      final next = await ref.read(playbackProvider.notifier).setSsh(widget.roomId, enabled: enabled);
      if (!mounted) {
        return;
      }
      setState(() {
        _ssh = next;
        _sshBusy = false;
      });
    } on Object catch (err) {
      if (!mounted) {
        return;
      }
      setState(() {
        _sshBusy = false;
        _error = userMessageFor(err);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final settings = _settings;
    final padding = MediaQuery.viewInsetsOf(context);
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 0, 20, 24 + padding.bottom),
      child: _loading
          ? const SizedBox(height: 180, child: Center(child: CircularProgressIndicator()))
          : SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text('Room settings', style: text.titleLarge?.copyWith(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 16),
                  TextField(
                    key: const Key('room-name-field'),
                    controller: _name,
                    textCapitalization: TextCapitalization.words,
                    decoration: const InputDecoration(labelText: 'Name', border: OutlineInputBorder()),
                    onEditingComplete: () {
                      if (settings != null) {
                        _persist(settings.copyWith(name: _name.text.trim()));
                      }
                    },
                  ),
                  const SizedBox(height: 8),
                  if (settings != null) ...[
                    _ToneSlider(
                      key: const Key('volume-limit-slider'),
                      label: 'Volume limit',
                      value: settings.volumeLimit.toDouble(),
                      min: 1,
                      max: 100,
                      divisions: 99,
                      display: '${settings.volumeLimit}',
                      onChanged: (value) => setState(() {
                        _settings = settings.copyWith(volumeLimit: value.round());
                      }),
                      onChangeEnd: (value) => _persist(settings.copyWith(volumeLimit: value.round())),
                    ),
                    _ToneSlider(
                      key: const Key('bass-slider'),
                      label: 'Bass',
                      value: settings.bass,
                      min: -10,
                      max: 10,
                      divisions: 20,
                      display: _db(settings.bass),
                      onChanged: (value) {
                        setState(() => _settings = settings.copyWith(bass: value));
                        _preview(settings.copyWith(bass: value));
                      },
                      onChangeEnd: (value) => _persist(settings.copyWith(bass: value)),
                    ),
                    _ToneSlider(
                      key: const Key('treble-slider'),
                      label: 'Treble',
                      value: settings.treble,
                      min: -10,
                      max: 10,
                      divisions: 20,
                      display: _db(settings.treble),
                      onChanged: (value) {
                        setState(() => _settings = settings.copyWith(treble: value));
                        _preview(settings.copyWith(treble: value));
                      },
                      onChangeEnd: (value) => _persist(settings.copyWith(treble: value)),
                    ),
                    _ToneSlider(
                      key: const Key('balance-slider'),
                      label: 'Balance',
                      value: settings.balance,
                      min: -10,
                      max: 10,
                      divisions: 20,
                      display: _balance(settings.balance),
                      leading: 'L',
                      trailing: 'R',
                      onChanged: (value) {
                        setState(() => _settings = settings.copyWith(balance: value));
                        _preview(settings.copyWith(balance: value));
                      },
                      onChangeEnd: (value) => _persist(settings.copyWith(balance: value)),
                    ),
                    SwitchListTile(
                      key: const Key('loudness-switch'),
                      contentPadding: EdgeInsets.zero,
                      title: const Text('Bass boost'),
                      subtitle: const Text('Loudness contour — a little extra low end'),
                      value: settings.loudness,
                      onChanged: (value) => _persist(settings.copyWith(loudness: value)),
                    ),
                    const SizedBox(height: 8),
                    InputDecorator(
                      decoration: const InputDecoration(labelText: 'Audio board', border: OutlineInputBorder()),
                      child: DropdownButtonHideUnderline(
                        child: DropdownButton<String>(
                          key: const Key('dac-dropdown'),
                          isExpanded: true,
                          value: dacOptions.any((o) => o.$1 == settings.dac) ? settings.dac : 'auto',
                          items: [
                            for (final option in dacOptions) DropdownMenuItem(value: option.$1, child: Text(option.$2)),
                          ],
                          onChanged: (value) {
                            if (value != null) {
                              _persist(settings.copyWith(dac: value));
                            }
                          },
                        ),
                      ),
                    ),
                    if (settings.dacRebootRequired) ...[
                      const SizedBox(height: 8),
                      Text(
                        'Restart this Pi for the audio board change to take effect.',
                        style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                      ),
                    ],
                  ],
                  const SizedBox(height: 16),
                  OutlinedButton.icon(
                    key: const Key('test-tone-button'),
                    onPressed: _toneBusy ? null : _testTone,
                    icon: _toneBusy
                        ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.graphic_eq),
                    label: Text(_toneBusy ? 'Playing test tone…' : 'Play test tone'),
                  ),
                  if (_toneResult != null) ...[
                    const SizedBox(height: 8),
                    Text(_toneResult!, key: const Key('test-tone-result'), style: text.bodySmall),
                  ],
                  if (_audioLines != null) ...[
                    const SizedBox(height: 4),
                    for (final line in _audioLines!)
                      Text(line, style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant)),
                  ],
                  const SizedBox(height: 20),
                  GestureDetector(
                    key: const Key('player-version-tap'),
                    onTap: _onVersionTap,
                    child: Text(
                      'FreeSounder $appVersion',
                      textAlign: TextAlign.center,
                      style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                    ),
                  ),
                  if (_developerUnlocked) ...[
                    const SizedBox(height: 16),
                    Text('Developer', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
                    const SizedBox(height: 4),
                    Text(
                      'SSH is off by default and turns itself off after a reboot. '
                      'The household API has no login of its own — only enable this on your own network.',
                      style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                    ),
                    SwitchListTile(
                      key: const Key('ssh-developer-switch'),
                      contentPadding: EdgeInsets.zero,
                      title: const Text('Enable SSH'),
                      value: _ssh?.enabled ?? false,
                      onChanged: _sshBusy ? null : (value) => _toggleSsh(value),
                    ),
                    if (_ssh?.command != null) ...[
                      SelectableText(_ssh!.command!, style: text.bodyMedium),
                      const SizedBox(height: 4),
                    ],
                    if (_ssh?.password != null) ...[
                      SelectableText('Password: ${_ssh!.password}', style: text.bodyMedium),
                      TextButton(
                        onPressed: () {
                          Clipboard.setData(ClipboardData(text: _ssh!.password!));
                        },
                        child: const Text('Copy password'),
                      ),
                    ],
                    if (_ssh?.warning != null)
                      Text(_ssh!.warning!, style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant)),
                  ],
                  if (_error != null) ...[
                    const SizedBox(height: 8),
                    Text(_error!, style: text.bodySmall?.copyWith(color: scheme.error)),
                  ],
                  if (_saving) const Padding(padding: EdgeInsets.only(top: 12), child: LinearProgressIndicator()),
                ],
              ),
            ),
    );
  }
}

class _ToneSlider extends StatelessWidget {
  const _ToneSlider({
    super.key,
    required this.label,
    required this.value,
    required this.min,
    required this.max,
    required this.divisions,
    required this.display,
    required this.onChanged,
    required this.onChangeEnd,
    this.leading,
    this.trailing,
  });

  final String label;
  final double value;
  final double min;
  final double max;
  final int divisions;
  final String display;
  final ValueChanged<double> onChanged;
  final ValueChanged<double> onChangeEnd;
  final String? leading;
  final String? trailing;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      children: [
        Row(
          children: [
            Text(label, style: text.titleSmall),
            const Spacer(),
            Text(display, style: text.labelMedium),
          ],
        ),
        Row(
          children: [
            if (leading != null) Text(leading!, style: text.labelSmall),
            Expanded(
              child: Slider(
                value: value.clamp(min, max),
                min: min,
                max: max,
                divisions: divisions,
                label: display,
                onChanged: onChanged,
                onChangeEnd: onChangeEnd,
              ),
            ),
            if (trailing != null) Text(trailing!, style: text.labelSmall),
          ],
        ),
      ],
    );
  }
}

String _db(double value) {
  if (value == 0) {
    return '0 dB';
  }
  final rounded = value % 1 == 0 ? value.toStringAsFixed(0) : value.toStringAsFixed(1);
  return '${value > 0 ? '+' : ''}$rounded dB';
}

String _balance(double value) {
  if (value == 0) {
    return 'Centre';
  }
  final amount = value.abs() % 1 == 0 ? value.abs().toStringAsFixed(0) : value.abs().toStringAsFixed(1);
  return value < 0 ? 'L $amount' : 'R $amount';
}
