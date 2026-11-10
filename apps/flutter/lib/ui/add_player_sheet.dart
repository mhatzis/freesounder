import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/nearby.dart';
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

enum _WizardStep { find, wifi, password, progress, adopt }

/// Wizard: find nearby (BLE + online) players, join Wi-Fi if needed, then adopt.
class AddPlayerSheet extends ConsumerStatefulWidget {
  const AddPlayerSheet({super.key, this.transport, this.phoneSsid});

  /// Optional override so widget tests do not need a radio.
  final NearbyTransport? transport;

  /// Optional prefilled phone SSID (tests / demo).
  final String? phoneSsid;

  @override
  ConsumerState<AddPlayerSheet> createState() => _AddPlayerSheetState();
}

class _AddPlayerSheetState extends ConsumerState<AddPlayerSheet> {
  final _name = TextEditingController();
  final _password = TextEditingController();
  _WizardStep _step = _WizardStep.find;
  List<NearbyPlayer> _ble = const [];
  List<UnclaimedPlayer> _online = const [];
  NearbyPlayer? _selected;
  List<WifiCandidate> _networks = const [];
  WifiCandidate? _network;
  String? _customSsid;
  var _loading = true;
  var _working = false;
  String? _error;
  String _progress = 'Connecting…';
  StreamSubscription<List<NearbyPlayer>>? _bleSub;
  NearbyTransport? _cachedTransport;

  NearbyTransport get _transport {
    final existing = _cachedTransport;
    if (existing != null) {
      return existing;
    }
    final NearbyTransport created = widget.transport ?? ref.read(nearbyTransportProvider);
    _cachedTransport = created;
    return created;
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        _startFind();
      }
    });
  }

  @override
  void dispose() {
    _bleSub?.cancel();
    final transport = _cachedTransport;
    if (transport != null) {
      unawaited(transport.stopScan());
      unawaited(transport.disconnect());
    }
    _name.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _startFind() async {
    setState(() {
      _step = _WizardStep.find;
      _loading = true;
      _error = null;
      _ble = const [];
    });
    await _bleSub?.cancel();
    _bleSub = _transport.players.listen((players) {
      if (!mounted) {
        return;
      }
      setState(() => _ble = players);
    });
    try {
      await _transport.startScan();
      final online = await ref.read(playbackProvider.notifier).listUnclaimed();
      if (!mounted) {
        return;
      }
      setState(() {
        _online = online;
        _loading = false;
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

  Future<void> _refreshFind() async {
    await _startFind();
  }

  Future<void> _pick(NearbyPlayer player) async {
    setState(() {
      _selected = player;
      _error = null;
      _working = true;
    });
    if (!player.needsWifi) {
      if (_name.text.isEmpty) {
        _name.text = _suggestedName(player.name);
      }
      setState(() {
        _working = false;
        _step = _WizardStep.adopt;
      });
      return;
    }
    try {
      await _transport.stopScan();
      await _transport.connect(player);
      final info = await _transport.deviceInfo();
      if (_name.text.isEmpty) {
        _name.text = _suggestedName(info.deviceName.isNotEmpty ? info.deviceName : player.name);
      }
      final networks = await _transport.scanWifi();
      final phone = widget.phoneSsid ?? await _transport.phoneSsid();
      WifiCandidate? prefill;
      if (phone != null && phone.isNotEmpty) {
        prefill = networks.where((n) => n.ssid == phone).firstOrNull ??
            WifiCandidate(ssid: phone, rssi: 0, auth: 'WPA2');
      }
      if (!mounted) {
        return;
      }
      setState(() {
        _networks = networks;
        _network = prefill ?? (networks.isNotEmpty ? networks.first : null);
        _working = false;
        _step = _WizardStep.wifi;
      });
    } on Object catch (err) {
      if (!mounted) {
        return;
      }
      setState(() {
        _working = false;
        _error = err.toString();
      });
    }
  }

  void _continueWifi() {
    final ssid = _network?.ssid ?? _customSsid?.trim();
    if (ssid == null || ssid.isEmpty) {
      setState(() => _error = 'Choose a Wi-Fi network.');
      return;
    }
    _network ??= WifiCandidate(ssid: ssid, rssi: 0, auth: 'WPA2');
    setState(() {
      _error = null;
      _step = (_network?.isOpen ?? false) ? _WizardStep.progress : _WizardStep.password;
    });
    if (_network?.isOpen ?? false) {
      unawaited(_provision());
    }
  }

  Future<void> _provision() async {
    final player = _selected;
    final network = _network;
    if (player == null || network == null) {
      return;
    }
    setState(() {
      _step = _WizardStep.progress;
      _working = true;
      _error = null;
      _progress = 'Connecting to ${network.ssid}…';
    });
    try {
      final result = await _transport.provision(
        ssid: network.ssid,
        password: _password.text,
        roomName: _name.text.trim().isEmpty ? null : _name.text.trim(),
      );
      if (!mounted) {
        return;
      }
      if (!result.ok) {
        setState(() {
          _working = false;
          _error = result.message;
          _progress = result.message;
        });
        return;
      }
      setState(() => _progress = result.message);
      final id = result.playerId ?? player.id;
      ref.read(playbackProvider.notifier).rememberProvisionedPlayer(
            id: id,
            name: player.name,
            address: result.ip,
          );
      final unclaimed = await _waitForUnclaimed(id);
      if (!mounted) {
        return;
      }
      final resolved = unclaimed ??
          UnclaimedPlayer(
            id: id,
            name: player.name,
            url: result.redirect ?? 'http://${result.ip ?? 'freesounder.local'}:8090',
            address: result.ip,
            port: 8090,
          );
      setState(() {
        _selected = NearbyPlayer(
          id: resolved.id,
          name: resolved.name,
          needsWifi: false,
          subtitle: resolved.address,
          unclaimed: resolved,
        );
        if (_name.text.isEmpty) {
          _name.text = _suggestedName(resolved.name);
        }
        _working = false;
        _step = _WizardStep.adopt;
      });
    } on Object catch (err) {
      if (!mounted) {
        return;
      }
      setState(() {
        _working = false;
        _error = err.toString();
        _progress = 'Could not finish setup.';
      });
    }
  }

  Future<UnclaimedPlayer?> _waitForUnclaimed(String id) async {
    const attempts = 15;
    for (var i = 0; i < attempts; i++) {
      final list = await ref.read(playbackProvider.notifier).listUnclaimed();
      for (final player in list) {
        if (player.id == id) {
          return player;
        }
      }
      await Future<void>.delayed(const Duration(milliseconds: 400));
    }
    return null;
  }

  Future<void> _adopt() async {
    final selected = _selected;
    final name = _name.text.trim();
    if (selected == null || name.isEmpty) {
      setState(() => _error = 'Give the room a name.');
      return;
    }
    setState(() {
      _working = true;
      _error = null;
    });
    try {
      final unclaimed = selected.unclaimed;
      await ref.read(playbackProvider.notifier).adoptPlayer(
            name: name,
            id: unclaimed?.id ?? selected.id,
            url: unclaimed?.url,
          );
      if (mounted) {
        Navigator.of(context).pop();
      }
    } on Object catch (err) {
      if (!mounted) {
        return;
      }
      setState(() {
        _working = false;
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
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxHeight: MediaQuery.sizeOf(context).height * 0.85,
        ),
        child: SingleChildScrollView(
          key: const Key('add-player-capture'),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('Add a player', style: text.titleLarge?.copyWith(fontWeight: FontWeight.w600)),
              const SizedBox(height: 4),
              Text(_subtitle, style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant)),
              const SizedBox(height: 16),
              if (_step == _WizardStep.find) _findStep(scheme, text),
              if (_step == _WizardStep.wifi) _wifiStep(scheme, text),
              if (_step == _WizardStep.password) _passwordStep(scheme, text),
              if (_step == _WizardStep.progress) _progressStep(scheme, text),
              if (_step == _WizardStep.adopt) _adoptStep(scheme, text),
              if (_error != null && _step != _WizardStep.progress) ...[
                const SizedBox(height: 8),
                Text(_error!, style: text.bodySmall?.copyWith(color: scheme.error)),
              ],
              const SizedBox(height: 8),
            ],
          ),
        ),
      ),
    );
  }

  String get _subtitle {
    switch (_step) {
      case _WizardStep.find:
        return 'Nearby boards that need Wi-Fi show up here, along with players already on this network.';
      case _WizardStep.wifi:
        return 'Choose the Wi-Fi this player should join.';
      case _WizardStep.password:
        return 'Enter the password for ${_network?.ssid ?? "this network"}. It is sent only to the player.';
      case _WizardStep.progress:
        return 'Joining ${_network?.ssid ?? "the network"}…';
      case _WizardStep.adopt:
        return 'Name the room and it joins this system.';
    }
  }

  Widget _findStep(ColorScheme scheme, TextTheme text) {
    final bleIds = {for (final p in _ble) p.id};
    final items = <NearbyPlayer>[
      ..._ble,
      for (final player in _online)
        if (!bleIds.contains(player.id))
          NearbyPlayer(
            id: player.id,
            name: player.name,
            needsWifi: false,
            subtitle: player.address ?? player.url,
            unclaimed: player,
          ),
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('Find nearby players', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        if (_loading && items.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 24),
            child: Center(child: CircularProgressIndicator()),
          )
        else if (items.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 16),
            child: Text(
              'No players found. Bring a new Pi close by, or plug it into Ethernet, then try again.',
              style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
            ),
          )
        else
          for (final player in items)
            ListTile(
              key: Key(player.needsWifi ? 'nearby-${player.id}' : 'unclaimed-${player.id}'),
              contentPadding: EdgeInsets.zero,
              leading: Icon(player.needsWifi ? Icons.bluetooth : Icons.lan_outlined),
              title: Text(player.name),
              subtitle: Text(player.subtitle ?? (player.needsWifi ? 'Needs Wi-Fi' : 'On the network')),
              trailing: _working && _selected?.id == player.id
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.chevron_right),
              onTap: _working ? null : () => _pick(player),
            ),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            onPressed: _loading || _working ? null : _refreshFind,
            icon: const Icon(Icons.refresh),
            label: const Text('Search again'),
          ),
        ),
      ],
    );
  }

  Widget _wifiStep(ColorScheme scheme, TextTheme text) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('Wi-Fi network', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        if (_networks.isEmpty)
          Text(
            'No networks reported yet. You can type a name below.',
            style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
          )
        else
          for (final network in _networks)
            RadioListTile<String>(
              key: Key('wifi-${network.ssid}'),
              value: network.ssid,
              groupValue: _network?.ssid,
              onChanged: _working
                  ? null
                  : (value) {
                      setState(() {
                        _network = network;
                        _error = null;
                      });
                    },
              title: Text(network.ssid),
              subtitle: Text('${network.auth == 'NO' ? 'Open' : network.auth}  ·  ${network.rssi} dBm'),
              contentPadding: EdgeInsets.zero,
            ),
        TextField(
          key: const Key('custom-ssid'),
          enabled: !_working,
          decoration: const InputDecoration(
            labelText: 'Or type a network name',
            border: OutlineInputBorder(),
          ),
          onChanged: (value) {
            _customSsid = value;
            if (value.trim().isNotEmpty) {
              setState(() => _network = WifiCandidate(ssid: value.trim(), rssi: 0, auth: 'WPA2'));
            }
          },
        ),
        const SizedBox(height: 16),
        FilledButton(
          key: const Key('wifi-continue'),
          onPressed: _working ? null : _continueWifi,
          child: const Text('Continue'),
        ),
        TextButton(
          onPressed: _working
              ? null
              : () {
                  unawaited(_transport.disconnect());
                  unawaited(_startFind());
                },
          child: const Text('Back'),
        ),
      ],
    );
  }

  Widget _passwordStep(ColorScheme scheme, TextTheme text) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          key: const Key('wifi-password'),
          controller: _password,
          enabled: !_working,
          obscureText: true,
          decoration: InputDecoration(
            labelText: 'Password for ${_network?.ssid ?? 'Wi-Fi'}',
            border: const OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          key: const Key('provision-room-name'),
          controller: _name,
          enabled: !_working,
          textCapitalization: TextCapitalization.words,
          decoration: const InputDecoration(
            labelText: 'Room name (optional)',
            hintText: 'Kitchen',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 16),
        FilledButton(
          key: const Key('wifi-join'),
          onPressed: _working ? null : () => unawaited(_provision()),
          child: const Text('Join network'),
        ),
        TextButton(
          onPressed: _working ? null : () => setState(() => _step = _WizardStep.wifi),
          child: const Text('Back'),
        ),
      ],
    );
  }

  Widget _progressStep(ColorScheme scheme, TextTheme text) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 16),
      child: Column(
        children: [
          if (_working) const CircularProgressIndicator(),
          const SizedBox(height: 16),
          Text(_progress, textAlign: TextAlign.center, style: text.bodyMedium),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!, textAlign: TextAlign.center, style: text.bodySmall?.copyWith(color: scheme.error)),
            const SizedBox(height: 12),
            TextButton(
              key: const Key('wifi-retry'),
              onPressed: () => setState(() => _step = _WizardStep.password),
              child: const Text('Try again'),
            ),
          ],
        ],
      ),
    );
  }

  Widget _adoptStep(ColorScheme scheme, TextTheme text) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (_selected != null)
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.speaker_outlined),
            title: Text(_selected!.name),
            subtitle: Text(_selected!.subtitle ?? 'On the network'),
          ),
        TextField(
          key: const Key('new-room-name'),
          controller: _name,
          enabled: !_working,
          textCapitalization: TextCapitalization.words,
          decoration: const InputDecoration(
            labelText: 'Room name',
            hintText: 'Kitchen',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 16),
        FilledButton(
          key: const Key('join-household'),
          onPressed: _working ? null : _adopt,
          child: _working
              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
              : const Text('Join this system'),
        ),
      ],
    );
  }
}

String _suggestedName(String advertised) {
  const prefix = 'freesounder-';
  if (advertised.toLowerCase().startsWith(prefix) && advertised.length > prefix.length) {
    final rest = advertised.substring(prefix.length);
    return rest[0].toUpperCase() + rest.substring(1);
  }
  if (advertised.startsWith('FreeSounder-') && advertised.length > 'FreeSounder-'.length) {
    return advertised;
  }
  return advertised;
}
