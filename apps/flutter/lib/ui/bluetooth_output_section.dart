import 'dart:async';

import 'package:flutter/material.dart';

import '../api/control_client.dart';
import '../api/errors.dart';
import '../models/bluetooth_output.dart';

/// What the Output section needs from the player (a fake in tests).
abstract class BtOutputApi {
  Future<BtOutputStatus> status();
  Future<List<BtSpeaker>> scan();
  Future<BtOutputStatus> connect(BtSpeaker speaker);
  Future<BtOutputStatus> use({required bool enabled});
  Future<BtOutputStatus> latency(int ms);
  Future<BtOutputStatus> forget();
}

class ClientBtOutputApi implements BtOutputApi {
  ClientBtOutputApi(this.client, this.roomId);

  final ControlApiClient client;
  final String roomId;

  @override
  Future<BtOutputStatus> status() => client.bluetoothStatus(roomId);
  @override
  Future<List<BtSpeaker>> scan() => client.bluetoothScan(roomId);
  @override
  Future<BtOutputStatus> connect(BtSpeaker speaker) => client.bluetoothConnect(roomId, speaker);
  @override
  Future<BtOutputStatus> use({required bool enabled}) => client.bluetoothUse(roomId, enabled: enabled);
  @override
  Future<BtOutputStatus> latency(int ms) => client.bluetoothLatency(roomId, ms);
  @override
  Future<BtOutputStatus> forget() => client.bluetoothForget(roomId);
}

/// EXPERIMENTAL: room settings "Output" — wired, or a paired Bluetooth speaker.
class BluetoothOutputSection extends StatefulWidget {
  const BluetoothOutputSection({super.key, required this.api, this.refreshEvery = const Duration(seconds: 5)});

  final BtOutputApi api;
  final Duration refreshEvery;

  @override
  State<BluetoothOutputSection> createState() => _BluetoothOutputSectionState();
}

class _BluetoothOutputSectionState extends State<BluetoothOutputSection> {
  BtOutputStatus? _status;
  List<BtSpeaker>? _found;
  String? _busy; // 'scan', 'connect:<mac>', 'use', 'forget'
  String? _error;
  double? _latencyDrag;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _refresh();
    _timer = Timer.periodic(widget.refreshEvery, (_) {
      if (_busy == null && _latencyDrag == null) _refresh();
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final status = await widget.api.status();
      if (mounted) {
        setState(() {
          _status = status;
          if (_busy == null) _error = null;
        });
      }
    } on Object catch (err) {
      if (mounted && _status == null) {
        final noBt = err is ControlApiException && err.statusCode == 404;
        setState(() => _error = noBt ? 'Bluetooth needs the BT image on this player.' : userMessageFor(err));
      }
    }
  }

  bool _retrying = false;

  Future<void> _retry() async {
    setState(() => _retrying = true);
    await _refresh();
    if (mounted) setState(() => _retrying = false);
  }

  Future<void> _run(String busy, Future<void> Function() work) async {
    setState(() {
      _busy = busy;
      _error = null;
    });
    try {
      await work();
    } on Object catch (err) {
      if (mounted) setState(() => _error = userMessageFor(err));
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  Future<void> _scan() => _run('scan', () async {
    final found = await widget.api.scan();
    if (mounted) setState(() => _found = found);
  });

  Future<void> _connect(BtSpeaker speaker) => _run('connect:${speaker.mac}', () async {
    final status = await widget.api.connect(speaker);
    if (mounted) {
      setState(() {
        _status = status;
        _found = null;
      });
    }
  });

  Future<void> _use(bool enabled) => _run('use', () async {
    final status = await widget.api.use(enabled: enabled);
    if (mounted) setState(() => _status = status);
  });

  Future<void> _forget() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Forget this speaker?'),
        content: const Text('The room goes back to its wired output. Pair the speaker again to use it.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            key: const Key('bt-forget-confirm'),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Forget'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    await _run('forget', () async {
      final status = await widget.api.forget();
      if (mounted) setState(() => _status = status);
    });
  }

  Future<void> _saveLatency(double value) async {
    final ms = value.round();
    try {
      final status = await widget.api.latency(ms);
      if (mounted) {
        setState(() {
          _status = status;
          _latencyDrag = null;
        });
      }
    } on Object catch (err) {
      if (mounted) {
        setState(() {
          _latencyDrag = null;
          _error = userMessageFor(err);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final status = _status;
    final header = Row(
      children: [
        Text('Output', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
        const SizedBox(width: 8),
        Container(
          key: const Key('bt-experimental-badge'),
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
          decoration: BoxDecoration(color: scheme.tertiaryContainer, borderRadius: BorderRadius.circular(999)),
          child: Text('Experimental', style: text.labelSmall?.copyWith(color: scheme.onTertiaryContainer)),
        ),
      ],
    );
    if (status != null && !status.available) {
      // Never hide the section: say why there is no Bluetooth and let the
      // user retry (the player tries to bring its adapter up on each check).
      return Column(
        key: const Key('bt-unavailable'),
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          header,
          const SizedBox(height: 4),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.bluetooth_disabled_rounded, color: scheme.error),
            title: const Text('Bluetooth speakers unavailable on this player'),
            subtitle: Text(
              status.reason ?? status.lastError ?? 'The player did not find its Bluetooth adapter.',
              key: const Key('bt-unavailable-reason'),
              style: TextStyle(color: scheme.onSurfaceVariant),
            ),
          ),
          Align(
            alignment: Alignment.centerLeft,
            child: OutlinedButton.icon(
              key: const Key('bt-retry'),
              onPressed: _retrying ? null : _retry,
              icon: _retrying
                  ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.refresh_rounded),
              label: const Text('Try again'),
            ),
          ),
        ],
      );
    }
    final speaker = status?.speaker;
    final busy = _busy != null || status?.busy != null;
    final useBt = status?.enabled ?? false;
    final latency = _latencyDrag ?? (status?.latencyMs ?? 200).toDouble().clamp(0, 500).toDouble();

    return Column(
      key: const Key('bt-output-section'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        header,
        const SizedBox(height: 4),
        if (status == null && _error != null) ...[
          // The player did not answer: say so and offer a retry, never spin.
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.error_outline_rounded, color: scheme.error),
            title: const Text('Could not read Bluetooth from this player'),
            subtitle: Text(
              _error!,
              key: const Key('bt-error'),
              style: TextStyle(color: scheme.onSurfaceVariant),
            ),
          ),
          Align(
            alignment: Alignment.centerLeft,
            child: OutlinedButton.icon(
              key: const Key('bt-retry'),
              onPressed: _retrying ? null : _retry,
              icon: _retrying
                  ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.refresh_rounded),
              label: const Text('Try again'),
            ),
          ),
        ] else if (status == null)
          const Padding(
            padding: EdgeInsets.all(12),
            child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
          )
        else ...[
          RadioGroup<bool>(
            groupValue: useBt,
            onChanged: (value) {
              if (value != null && !busy && speaker != null) _use(value);
            },
            child: Column(
              children: [
                RadioListTile<bool>(
                  key: const Key('bt-output-wired'),
                  contentPadding: EdgeInsets.zero,
                  value: false,
                  enabled: speaker != null && !busy,
                  title: const Text('Wired output'),
                  subtitle: const Text('Audio board, USB or HDMI'),
                  secondary: const Icon(Icons.speaker_outlined),
                ),
                RadioListTile<bool>(
                  key: const Key('bt-output-bluetooth'),
                  contentPadding: EdgeInsets.zero,
                  value: true,
                  enabled: speaker != null && !busy,
                  title: Text(speaker?.name ?? 'Bluetooth speaker'),
                  subtitle: Text(status.summary, key: const Key('bt-status-line')),
                  secondary: Icon(
                    status.active ? Icons.bluetooth_audio : Icons.bluetooth,
                    color: status.active ? scheme.primary : null,
                  ),
                ),
              ],
            ),
          ),
          if (speaker != null) ...[
            _LatencySlider(
              value: latency,
              onChanged: (v) => setState(() => _latencyDrag = v),
              onChangeEnd: _saveLatency,
            ),
            Text(
              'If this room sounds behind the others, raise the delay; if it sounds ahead, lower it.',
              style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
            ),
          ],
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            children: [
              OutlinedButton.icon(
                key: const Key('bt-scan-button'),
                onPressed: busy ? null : _scan,
                icon: _busy == 'scan'
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.bluetooth_searching),
                label: Text(
                  _busy == 'scan'
                      ? 'Looking for speakers…'
                      : speaker == null
                      ? 'Find speakers'
                      : 'Use another speaker',
                ),
              ),
              if (speaker != null)
                TextButton(
                  key: const Key('bt-forget-button'),
                  onPressed: busy ? null : _forget,
                  child: const Text('Forget speaker'),
                ),
            ],
          ),
          if (_found != null) ...[
            const SizedBox(height: 4),
            if (_found!.isEmpty)
              Text(
                'No speakers found. Put the speaker in pairing mode (usually hold its Bluetooth button) '
                'and keep it near the player.',
                key: const Key('bt-none-found'),
                style: text.bodySmall,
              )
            else
              for (final s in _found!)
                ListTile(
                  key: Key('bt-found-${s.mac}'),
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  leading: const Icon(Icons.speaker_group_outlined),
                  title: Text(s.name),
                  subtitle: Text(s.paired ? 'Paired before' : s.mac),
                  trailing: _busy == 'connect:${s.mac}'
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.chevron_right),
                  onTap: busy ? null : () => _connect(s),
                ),
          ] else if (speaker == null)
            Text(
              'Put your speaker in pairing mode, then tap Find speakers.',
              style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
            ),
          if (status.warning != null) ...[
            const SizedBox(height: 8),
            Row(
              key: const Key('bt-radio-warning'),
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.warning_amber_rounded, size: 18, color: scheme.error),
                const SizedBox(width: 6),
                Expanded(child: Text(status.warning!, style: text.bodySmall)),
              ],
            ),
          ],
        ],
        if (status != null && (_error != null || status.lastError != null)) ...[
          const SizedBox(height: 6),
          Text(
            _error ?? status.lastError!,
            key: const Key('bt-error'),
            style: text.bodySmall?.copyWith(color: scheme.error),
          ),
        ],
      ],
    );
  }
}

class _LatencySlider extends StatelessWidget {
  const _LatencySlider({required this.value, required this.onChanged, required this.onChangeEnd});

  final double value;
  final ValueChanged<double> onChanged;
  final ValueChanged<double> onChangeEnd;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      children: [
        Row(
          children: [
            Text('Bluetooth delay', style: text.titleSmall),
            const Spacer(),
            Text('${value.round()} ms', key: const Key('bt-latency-value'), style: text.labelMedium),
          ],
        ),
        Slider(
          key: const Key('bt-latency-slider'),
          value: value,
          min: 0,
          max: 500,
          divisions: 50,
          label: '${value.round()} ms',
          onChanged: onChanged,
          onChangeEnd: onChangeEnd,
        ),
      ],
    );
  }
}
