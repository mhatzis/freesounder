import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/connection_prefs.dart';
import '../api/errors.dart';

import '../app_version.dart';
import '../models/player_settings.dart';
import '../state/playback_store.dart';
import 'audio_board_picker.dart';
import 'bluetooth_output_section.dart';

/// Boards that drive speakers directly; they start with a safe volume limit.
const ampDacs = {
  'hifiberry-amp2',
  'hifiberry-amp3',
  'hifiberry-amp4',
  'hifiberry-amp4pro',
  'hifiberry-amp100',
  'digiampplus',
  'iqaudio-digiampplus',
  'pcm5122-amp',
  'max98357a',
};

const _sheetInitial = 0.92;
const _sheetMin = 0.55;

/// Room settings as a tall sheet that always closes: drag the handle (or the
/// list from its top) down, fling it, tap outside, the X, or Android back.
Future<void> showRoomSettingsSheet(BuildContext context, {required String roomId, String? roomName}) {
  final sheet = DraggableScrollableController();
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    isDismissible: true,
    enableDrag: true,
    builder: (context) => DraggableScrollableSheet(
      controller: sheet,
      expand: false,
      initialChildSize: _sheetInitial,
      minChildSize: _sheetMin,
      maxChildSize: 1,
      shouldCloseOnMinExtent: true,
      builder: (context, scroll) =>
          RoomSettingsSheet(roomId: roomId, roomName: roomName, controller: scroll, sheet: sheet),
    ),
  ).whenComplete(sheet.dispose);
}

/// Per-player name, tone, volume limit and DAC.
class RoomSettingsSheet extends ConsumerStatefulWidget {
  const RoomSettingsSheet({super.key, required this.roomId, this.roomName, this.controller, this.sheet});

  final String roomId;
  final String? roomName;

  /// The sheet's scroll controller: scrolling past the top shrinks the sheet.
  final ScrollController? controller;

  /// Drives the sheet size while the handle is dragged.
  final DraggableScrollableController? sheet;

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

  var _rebooting = false;
  var _resetting = false;

  Future<void> _factoryReset() async {
    final scheme = Theme.of(context).colorScheme;
    final ok = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        key: const Key('factory-reset-dialog'),
        icon: Icon(Icons.warning_amber_rounded, color: scheme.error),
        title: const Text('Factory reset this player?'),
        content: const Text(
          'It forgets its Wi-Fi, its home, its room name and all room settings, then restarts '
          'in Bluetooth setup mode so you can add it again. This cannot be undone.',
        ),
        actions: [
          TextButton(
            key: const Key('factory-reset-cancel'),
            onPressed: () => Navigator.of(dialog).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const Key('factory-reset-confirm'),
            style: FilledButton.styleFrom(backgroundColor: scheme.error, foregroundColor: scheme.onError),
            onPressed: () => Navigator.of(dialog).pop(true),
            child: const Text('Factory reset'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) {
      return;
    }
    setState(() => _resetting = true);
    try {
      await ref.read(playbackProvider.notifier).factoryResetPlayer(widget.roomId);
      if (!mounted) {
        return;
      }
      final messenger = ScaffoldMessenger.maybeOf(context);
      Navigator.of(context).maybePop();
      messenger?.showSnackBar(
        const SnackBar(content: Text('Resetting the player. It restarts in setup mode in about a minute.')),
      );
    } on Object catch (err) {
      if (mounted) {
        setState(() {
          _resetting = false;
          _error = userMessageFor(err);
        });
      }
    }
  }

  Future<void> _reboot() async {
    setState(() => _rebooting = true);
    try {
      await ref.read(playbackProvider.notifier).rebootPlayer(widget.roomId);
    } on Object catch (err) {
      if (mounted) {
        setState(() {
          _rebooting = false;
          _error = userMessageFor(err);
        });
      }
    }
  }

  bool _diagBusy = false;
  String? _diag;

  Future<void> _loadDiagnostics() async {
    setState(() => _diagBusy = true);
    String text;
    try {
      final client = ref.read(playbackProvider.notifier).client;
      if (client == null) {
        throw StateError('Not connected');
      }
      text = formatDiagnostics(await client.diagnostics(widget.roomId));
    } on Object catch (err) {
      text = 'Could not read diagnostics: ${userMessageFor(err)}';
    }
    if (!mounted) {
      return;
    }
    setState(() {
      _diagBusy = false;
      _diag = text;
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
    return CustomScrollView(
      key: const Key('room-settings-scroll'),
      controller: widget.controller,
      slivers: [
        SliverPersistentHeader(
          pinned: true,
          delegate: SheetHeader(
            title: 'Room settings',
            color: Theme.of(context).bottomSheetTheme.modalBackgroundColor ?? scheme.surfaceContainerLow,
            sheet: widget.sheet,
            initialSize: _sheetInitial,
            minSize: _sheetMin,
          ),
        ),
        SliverPadding(
          padding: EdgeInsets.fromLTRB(20, 0, 20, 24 + padding.bottom),
          sliver: SliverToBoxAdapter(
            child: _loading
                ? const SizedBox(height: 180, child: Center(child: CircularProgressIndicator()))
                : Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      const SizedBox(height: 4),
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
                        Builder(
                          builder: (context) {
                            final board = audioBoardFor(settings.dac);
                            return InputDecorator(
                              decoration: const InputDecoration(
                                labelText: 'Audio board',
                                border: OutlineInputBorder(),
                                contentPadding: EdgeInsets.fromLTRB(12, 4, 4, 4),
                              ),
                              child: ListTile(
                                key: const Key('dac-picker-tile'),
                                contentPadding: EdgeInsets.zero,
                                dense: true,
                                title: Text(board.title, maxLines: 1, overflow: TextOverflow.ellipsis),
                                subtitle: board.subtitle == null
                                    ? null
                                    : Text(board.subtitle!, maxLines: 1, overflow: TextOverflow.ellipsis),
                                trailing: const Icon(Icons.chevron_right),
                                onTap: () async {
                                  final picked = await showAudioBoardPicker(context, current: settings.dac);
                                  final now = _settings;
                                  if (picked != null && now != null && picked != now.dac && mounted) {
                                    await _persist(now.copyWith(dac: picked));
                                  }
                                },
                              ),
                            );
                          },
                        ),
                        if (ampDacs.contains(settings.dac)) ...[
                          const SizedBox(height: 8),
                          Text(
                            'Amp board: volume starts capped at 80% (headroom for tone and loudness, no clipping). '
                            'Raise the volume limit above if your speakers need it.',
                            key: const Key('amp-note'),
                            style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                          ),
                        ],
                        if (settings.dacRebootRequired) ...[
                          const SizedBox(height: 8),
                          Text(
                            'Restart this Pi for the audio board change to take effect.',
                            style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                          ),
                          Align(
                            alignment: Alignment.centerLeft,
                            child: TextButton.icon(
                              key: const Key('reboot-player-button'),
                              onPressed: _rebooting ? null : _reboot,
                              icon: const Icon(Icons.restart_alt),
                              label: Text(_rebooting ? 'Restarting… back in about a minute' : 'Restart player now'),
                            ),
                          ),
                        ],
                      ],
                      if (ref.read(playbackProvider.notifier).client case final client?) ...[
                        const SizedBox(height: 20),
                        BluetoothOutputSection(api: ClientBtOutputApi(client, widget.roomId)),
                      ],
                      const SizedBox(height: 16),
                      OutlinedButton.icon(
                        key: const Key('test-tone-button'),
                        onPressed: _toneBusy ? null : _testTone,
                        icon: _toneBusy
                            ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.graphic_eq),
                        label: Text(_toneBusy ? 'Playing test sound…' : 'Play test sound'),
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
                      const SizedBox(height: 24),
                      OutlinedButton.icon(
                        key: const Key('factory-reset-button'),
                        style: OutlinedButton.styleFrom(
                          foregroundColor: scheme.error,
                          side: BorderSide(color: scheme.error),
                        ),
                        onPressed: _resetting ? null : _factoryReset,
                        icon: const Icon(Icons.restart_alt),
                        label: Text(_resetting ? 'Resetting…' : 'Factory reset'),
                      ),
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
                      if (_developerUnlocked || ref.watch(developerModeProvider)) ...[
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
                        const SizedBox(height: 8),
                        OutlinedButton.icon(
                          key: const Key('diagnostics-button'),
                          onPressed: _diagBusy ? null : _loadDiagnostics,
                          icon: const Icon(Icons.monitor_heart_outlined),
                          label: Text(_diagBusy ? 'Reading diagnostics…' : 'Diagnostics (last shutdown, power)'),
                        ),
                        if (_diag != null) ...[
                          const SizedBox(height: 8),
                          SelectableText(_diag!, key: const Key('diagnostics-text'), style: text.bodySmall),
                        ],
                      ],
                      if (_error != null) ...[
                        const SizedBox(height: 8),
                        Text(_error!, style: text.bodySmall?.copyWith(color: scheme.error)),
                      ],
                      if (_saving) const Padding(padding: EdgeInsets.only(top: 12), child: LinearProgressIndicator()),
                    ],
                  ),
          ),
        ),
      ],
    );
  }
}

/// Pinned sheet header: grab handle, title and a close button. Dragging it
/// moves the whole sheet (even when the list below is scrolled) and closes it
/// past the minimum size or on a downward fling.
class SheetHeader extends SliverPersistentHeaderDelegate {
  SheetHeader({required this.title, required this.color, this.sheet, this.initialSize = 0.92, this.minSize = 0.55});

  final String title;
  final Color color;
  final DraggableScrollableController? sheet;
  final double initialSize;
  final double minSize;

  static const height = 64.0;

  @override
  double get minExtent => height;

  @override
  double get maxExtent => height;

  @override
  bool shouldRebuild(SheetHeader old) => old.title != title || old.color != color || old.sheet != sheet;

  @override
  Widget build(BuildContext context, double shrinkOffset, bool overlapsContent) {
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    var dragged = 0.0;
    void close() => Navigator.of(context).maybePop();
    return GestureDetector(
      key: const Key('sheet-drag-handle'),
      behavior: HitTestBehavior.opaque,
      onVerticalDragStart: (_) => dragged = 0,
      onVerticalDragUpdate: (d) {
        dragged += d.delta.dy;
        final s = sheet;
        if (s != null && s.isAttached) {
          final h = MediaQuery.sizeOf(context).height;
          s.jumpTo((s.size - d.delta.dy / h).clamp(minSize, 1.0));
        }
      },
      onVerticalDragEnd: (d) {
        final s = sheet;
        final fling = (d.primaryVelocity ?? 0) > 700;
        final attached = s != null && s.isAttached;
        final low = attached ? s.size <= minSize + 0.04 : dragged > 80;
        if (fling || low) {
          close();
        } else if (attached) {
          s.animateTo(initialSize, duration: const Duration(milliseconds: 180), curve: Curves.easeOut);
        }
      },
      child: Material(
        color: color,
        child: Column(
          children: [
            const SizedBox(height: 10),
            Container(
              width: 36,
              height: 4,
              decoration: BoxDecoration(
                color: scheme.onSurfaceVariant.withValues(alpha: 0.4),
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.only(left: 20, right: 8),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(title, style: text.titleLarge?.copyWith(fontWeight: FontWeight.w600)),
                    ),
                    IconButton(
                      key: const Key('sheet-close'),
                      tooltip: 'Close',
                      icon: const Icon(Icons.close),
                      onPressed: close,
                    ),
                  ],
                ),
              ),
            ),
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

/// Diagnostics from the player as plain text (developer mode).
String formatDiagnostics(Map<String, dynamic> d) {
  List<String> lines(Object? v) => v is List ? [for (final x in v) '$x'] : (v == null ? const [] : ['$v']);
  final flags = lines(d['throttled_flags']);
  final prev = d['previous_boot'] is Map ? Map<String, dynamic>.from(d['previous_boot'] as Map) : null;
  final reset = lines(d['pmic_reset_reason']);
  final history = d['power_history'] is List ? d['power_history'] as List : const [];
  String hist(Object? e) {
    if (e is! Map) return '$e';
    final t = e['time'] is num
        ? DateTime.fromMillisecondsSinceEpoch(((e['time'] as num) * 1000).round()).toLocal().toString().substring(0, 19)
        : '?';
    final up = e['uptime'] is num ? ' up ${((e['uptime'] as num) / 60).toStringAsFixed(1)} min' : '';
    final mhz = e['cpu_mhz'] != null ? '  ${e['cpu_mhz']} MHz' : '';
    final top = topLine(e['top']);
    return '$t$up  ${e['throttled']}  ${e['ext5v'] ?? '?'} V  ${e['temp'] ?? '?'}°C$mhz'
        '${top.isEmpty ? '' : '  top: $top'}';
  }

  String load(Object? v) => v is List ? v.map((x) => x is num ? x.toStringAsFixed(2) : '$x').join(' / ') : '?';

  return [
    'Previous boot ended: ${d['previous_boot_ended'] ?? 'unknown'}',
    if (prev != null) ...[
      'Last heartbeat: ${prev['last_heartbeat'] ?? '?'} (up ${prev['uptime_at_end_min'] ?? '?'} min)',
      'Clean shutdown: ${prev['clean_shutdown'] == true ? 'yes (${prev['shutdown'] ?? 'shutdown'})' : 'no'}',
      'Input voltage then: ${prev['ext5v_last'] ?? '?'} V, lowest ${prev['ext5v_min'] ?? '?'} V',
      'Power flags then: ${prev['throttled'] ?? '?'}'
          '${lines(prev['throttled_flags']).isEmpty ? '' : ' (${lines(prev['throttled_flags']).join(', ')})'}',
      if (prev['temp'] != null || prev['load'] != null)
        'CPU then: ${prev['temp'] ?? '?'}°C, ${prev['cpu_mhz'] ?? '?'} MHz'
            '${prev['clock_capped'] == true ? ' (capped)' : ''}, load ${load(prev['load'])}',
      if (topLine(prev['top']).isNotEmpty) 'Top processes then: ${topLine(prev['top'])}',
    ],
    if (d['pmic_reset'] != null)
      'Pi 5 PMIC reset reason: ${d['pmic_reset']} (${reset.isEmpty ? 'none' : reset.join(', ')})',
    if (d['psu_max_current_ma'] != null)
      'Power supply: ${d['psu_max_current_ma']} mA${(d['psu_max_current_ma'] as num) < 5000 ? ' (not a 5 A supply)' : ''}',
    if (d['usb_over_current_at_boot'] == true) 'USB over-current at boot: yes',
    if (d['bootloader_rsts'] != null) 'Bootloader RSTS: ${d['bootloader_rsts']}',
    '',
    'Now: ${d['throttled'] ?? '?'} ${flags.isEmpty ? '(ok)' : '(${flags.join(', ')})'}',
    if (d['input_voltage'] != null)
      'Input voltage: ${d['input_voltage']}'
          '${d['input_voltage_min_this_boot'] != null ? ', lowest this boot ${d['input_voltage_min_this_boot']} V' : ''}',
    if (d['temperature'] != null) 'Temperature: ${d['temperature']}',
    if (d['cpu_load'] != null)
      'CPU load (1/5/15 min): ${load(d['cpu_load'])}${d['cpu_busy'] != null ? ', busy ${d['cpu_busy']}' : ''}',
    if (d['cpu_mhz'] != null)
      'CPU clock: ${d['cpu_mhz']} MHz (max ${d['cpu_max_mhz'] ?? '?'} of ${d['cpu_hw_max_mhz'] ?? '?'} MHz, '
          '${d['cpu_governor'] ?? '?'}${d['cpu_clock_capped'] == true ? ', capped for heat' : ''}'
          '${d['cpu_mode'] != null && d['cpu_mode'] != 'auto' ? ', mode ${d['cpu_mode']}' : ''})',
    if (topLine(d['top_processes']).isNotEmpty) 'Top processes: ${topLine(d['top_processes'])}',
    if (d['boots_in_journal'] != null) 'Boots in journal: ${d['boots_in_journal']}',
    if (history.isNotEmpty) ...['', 'Power flag history:', ...history.map(hist)],
    if (lines(d['power_log']).isNotEmpty) ...['', 'Power & button log:', ...lines(d['power_log'])],
    if (d.containsKey('bt_adapters')) ...[
      '',
      'Bluetooth: adapters ${lines(d['bt_adapters']).isEmpty ? 'none' : lines(d['bt_adapters']).join(', ')}, '
          'bluetoothd ${d['bt_bluetoothd'] ?? '?'}, daemon started ${d['daemon_starts_this_boot'] ?? '?'}× this boot',
      ...lines(d['bt_rfkill']).map((l) => 'rfkill: $l'),
      ...lines(d['bt_log']),
    ],
    if (lines(d['previous_boot_errors']).isNotEmpty) ...[
      '',
      'Previous boot warnings/errors:',
      ...lines(d['previous_boot_errors']),
    ],
    if (lines(d['previous_boot_tail']).isNotEmpty) ...[
      '',
      'Previous boot, last lines:',
      ...lines(d['previous_boot_tail']),
    ],
  ].join('\n');
}

/// "snapserver 3.1%, ffmpeg 1.2%" from the daemon's top-process list (% of one core).
String topLine(Object? v) {
  if (v is! List) return '';
  return [
    for (final p in v)
      if (p is Map) '${p['name'] ?? '?'} ${p['cpu'] ?? '?'}%',
  ].join(', ');
}
