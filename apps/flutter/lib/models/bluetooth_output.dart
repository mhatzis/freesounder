/// Experimental feature: a room's Bluetooth speaker output.
class BtSpeaker {
  const BtSpeaker({required this.mac, required this.name, this.paired = false, this.connected = false, this.rssi});

  final String mac;
  final String name;
  final bool paired;
  final bool connected;
  final int? rssi;

  factory BtSpeaker.fromJson(Map<String, dynamic> json) {
    final mac = json['mac'] as String? ?? '';
    final name = json['name'] as String?;
    return BtSpeaker(
      mac: mac,
      name: (name == null || name.isEmpty) ? mac : name,
      paired: json['paired'] as bool? ?? false,
      connected: json['connected'] as bool? ?? false,
      rssi: (json['rssi'] as num?)?.toInt(),
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
  });

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
    );
  }

  /// One short line for the status row.
  String get summary {
    final s = speaker;
    if (s == null) return 'Not set up';
    if (!enabled) return 'Paired, using the wired output';
    if (active) return 'Playing${codec != null ? ' · $codec' : ''}';
    if (s.connected) return 'Connected, starting…';
    return 'Not connected — wired output plays until it is back';
  }
}
