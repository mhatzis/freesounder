class PlayerSettings {
  const PlayerSettings({
    required this.name,
    this.volumeLimit = 100,
    this.bass = 0,
    this.treble = 0,
    this.balance = 0,
    this.loudness = false,
    this.dac = 'none',
    this.dacRebootRequired = false,
  });

  final String name;
  final int volumeLimit;
  final double bass;
  final double treble;
  final double balance;
  final bool loudness;
  final String dac;
  final bool dacRebootRequired;

  factory PlayerSettings.fromJson(Map<String, dynamic> json) {
    return PlayerSettings(
      name: json['name'] as String? ?? '',
      volumeLimit: json['volume_limit'] as int? ?? 100,
      bass: (json['bass'] as num?)?.toDouble() ?? 0,
      treble: (json['treble'] as num?)?.toDouble() ?? 0,
      balance: (json['balance'] as num?)?.toDouble() ?? 0,
      loudness: json['loudness'] as bool? ?? false,
      dac: json['dac'] as String? ?? 'none',
      dacRebootRequired: json['dac_reboot_required'] as bool? ?? false,
    );
  }

  Map<String, dynamic> toPatch() {
    return {
      'name': name,
      'volume_limit': volumeLimit,
      'bass': bass,
      'treble': treble,
      'balance': balance,
      'loudness': loudness,
      'dac': dac,
    };
  }

  PlayerSettings copyWith({
    String? name,
    int? volumeLimit,
    double? bass,
    double? treble,
    double? balance,
    bool? loudness,
    String? dac,
    bool? dacRebootRequired,
  }) {
    return PlayerSettings(
      name: name ?? this.name,
      volumeLimit: volumeLimit ?? this.volumeLimit,
      bass: bass ?? this.bass,
      treble: treble ?? this.treble,
      balance: balance ?? this.balance,
      loudness: loudness ?? this.loudness,
      dac: dac ?? this.dac,
      dacRebootRequired: dacRebootRequired ?? this.dacRebootRequired,
    );
  }
}

class UnclaimedPlayer {
  const UnclaimedPlayer({
    required this.id,
    required this.name,
    required this.url,
    this.address,
    this.port,
    this.role,
    this.state = 'unconfigured',
  });

  final String id;
  final String name;
  final String url;
  final String? address;
  final int? port;
  final String? role;
  final String state;

  factory UnclaimedPlayer.fromJson(Map<String, dynamic> json) {
    return UnclaimedPlayer(
      id: json['id'] as String,
      name: json['name'] as String? ?? json['id'] as String,
      url: json['url'] as String? ?? '',
      address: json['address'] as String?,
      port: json['port'] as int?,
      role: json['role'] as String?,
      state: json['state'] as String? ?? 'unconfigured',
    );
  }
}

class DiscoveredHost {
  const DiscoveredHost({required this.url, this.name = 'FreeSounder'});

  final String url;
  final String name;
}
