/// Experimental feature: a room's Bluetooth speaker output.
class BtSpeaker {
  const BtSpeaker({
    required this.mac,
    required this.name,
    this.paired = false,
    this.connected = false,
    this.rssi,
    this.hasName = true,
    this.kind = 'speaker',
    this.signal,
  });

  final String mac;

  /// What to show: the speaker's own name, or a friendly stand-in while the
  /// player has not learned it yet (see [hasName]).
  final String name;
  final bool paired;
  final bool connected;
  final int? rssi;

  /// False when the player did not learn the speaker's name during the scan.
  final bool hasName;

  /// 'speaker', 'headphones', 'car' or 'other' (from the Bluetooth class).
  final String kind;

  /// 'strong', 'good' or 'weak' (from the signal strength), if known.
  final String? signal;

  /// The last two bytes of the address, to tell unnamed speakers apart.
  String get shortMac {
    final parts = mac.split(':');
    return parts.length >= 2 ? parts.sublist(parts.length - 2).join(':') : mac;
  }

  factory BtSpeaker.fromJson(Map<String, dynamic> json) {
    final mac = json['mac'] as String? ?? '';
    final raw = (json['name'] as String?)?.trim();
    // Players before 0.6.18 sent the address (or BlueZ's dashed form of it)
    // when the name was not known yet.
    final looksLikeMac = raw != null && RegExp(r'^([0-9A-Fa-f]{2}[-:_]){5}[0-9A-Fa-f]{2}$').hasMatch(raw);
    final hasName = raw != null && raw.isNotEmpty && !looksLikeMac;
    final parts = mac.split(':');
    final tail = parts.length >= 2 ? parts.sublist(parts.length - 2).join(':') : mac;
    return BtSpeaker(
      mac: mac,
      name: hasName ? raw : 'Bluetooth speaker ($tail)',
      hasName: hasName,
      paired: json['paired'] as bool? ?? false,
      connected: json['connected'] as bool? ?? false,
      rssi: (json['rssi'] as num?)?.toInt(),
      kind: json['kind'] as String? ?? 'speaker',
      signal: json['signal'] as String?,
    );
  }
}

class BtOutputStatus {
  const BtOutputStatus({
    this.available = false,
    this.enabled = false,
    this.active = false,
    this.speaker,
    this.codec,
    this.latencyMs = 200,
    this.defaultLatencyMs = 200,
    this.busy,
    this.lastError,
    this.reason,
    this.warning,
    this.output,
    this.keepalive = true,
    this.quality = 'high',
  });

  /// Keep the speaker awake while nothing plays (0.6.19).
  final bool keepalive;

  /// 'high' or 'robust' (less radio airtime; for a weak or busy link).
  final String quality;

  /// The player has a Bluetooth adapter and the experimental output.
  final bool available;

  /// The speaker is chosen as this room's output (it may be away right now).
  final bool enabled;

  /// Audio is going to the speaker right now (else the wired output plays).
  final bool active;
  final BtSpeaker? speaker;
  final String? codec;
  final int latencyMs;
  final int defaultLatencyMs;
  final String? busy;
  final String? lastError;

  /// Why [available] is false (no adapter, BlueZ unreachable, ...).
  final String? reason;
  final String? warning;
  final String? output;

  factory BtOutputStatus.fromJson(Map<String, dynamic> json) {
    final speaker = json['speaker'];
    return BtOutputStatus(
      available: json['available'] as bool? ?? false,
      enabled: json['enabled'] as bool? ?? false,
      active: json['active'] as bool? ?? false,
      speaker: speaker is Map ? BtSpeaker.fromJson(Map<String, dynamic>.from(speaker)) : null,
      codec: json['codec'] as String?,
      latencyMs: (json['latency_ms'] as num?)?.toInt() ?? 200,
      defaultLatencyMs: (json['default_latency_ms'] as num?)?.toInt() ?? 200,
      busy: json['busy'] as String?,
      lastError: json['last_error'] as String?,
      reason: json['reason'] as String?,
      warning: json['warning'] as String?,
      output: json['output'] as String?,
      keepalive: json['keepalive'] as bool? ?? true,
      quality: json['quality'] as String? ?? 'high',
    );
  }

  /// One short line for the status row.
  String get summary {
    final s = speaker;
    if (s == null) return 'Not set up';
    if (!enabled) return 'Paired, using the wired output';
    if (active) return 'Playing${codec != null ? ' · $codec' : ''}';
    if (s.connected) return 'Connected, starting…';
    return 'Not connected — nothing plays here until it is back (trying every 10 s)';
  }
}
