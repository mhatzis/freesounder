class FormOption {
  const FormOption({required this.value, required this.title, this.disabled = false, this.description});

  final dynamic value;
  final String title;
  final bool disabled;
  final String? description;

  factory FormOption.fromJson(Map<String, dynamic> json) {
    return FormOption(
      value: json['value'],
      title: json['title'] as String? ?? '${json['value']}',
      disabled: json['disabled'] as bool? ?? false,
      description: json['description'] as String?,
    );
  }
}

class ConfigField {
  const ConfigField({
    required this.key,
    required this.type,
    this.label,
    this.description,
    this.requiredField = false,
    this.value,
    this.hasValue = false,
    this.secret = false,
    this.options = const [],
    this.hidden = false,
    this.advanced = false,
    this.readOnly = false,
    this.defaultValue,
  });

  final String key;
  final String type;
  final String? label;
  final String? description;
  final bool requiredField;
  final dynamic value;
  final bool hasValue;
  final bool secret;
  final List<FormOption> options;
  final bool hidden;
  final bool advanced;
  final bool readOnly;
  final dynamic defaultValue;

  factory ConfigField.fromJson(Map<String, dynamic> json) {
    return ConfigField(
      key: json['key'] as String,
      type: json['type'] as String? ?? 'string',
      label: json['label'] as String?,
      description: json['description'] as String?,
      requiredField: json['required'] as bool? ?? false,
      value: json['value'],
      hasValue: json['has_value'] as bool? ?? false,
      secret: json['secret'] as bool? ?? false,
      options: [
        for (final item in json['options'] as List? ?? const [])
          FormOption.fromJson(item as Map<String, dynamic>),
      ],
      hidden: json['hidden'] as bool? ?? false,
      advanced: json['advanced'] as bool? ?? false,
      readOnly: json['read_only'] as bool? ?? false,
      defaultValue: json['default'],
    );
  }
}

class MusicServiceType {
  const MusicServiceType({
    required this.domain,
    required this.name,
    this.category = 'streaming',
    this.authKind = 'credentials',
    this.description,
    this.availableOnServer = false,
    this.maStage,
    this.limited = false,
    this.limitation,
    this.multiInstance = false,
    this.configured = false,
  });

  final String domain;
  final String name;
  final String category;
  final String authKind;
  final String? description;
  final bool availableOnServer;
  final String? maStage;
  final bool limited;
  final String? limitation;
  final bool multiInstance;
  final bool configured;

  factory MusicServiceType.fromJson(Map<String, dynamic> json) {
    return MusicServiceType(
      domain: json['domain'] as String,
      name: json['name'] as String? ?? json['domain'] as String,
      category: json['category'] as String? ?? 'streaming',
      authKind: json['auth_kind'] as String? ?? 'credentials',
      description: json['description'] as String?,
      availableOnServer: json['available_on_server'] as bool? ?? false,
      maStage: json['ma_stage'] as String?,
      limited: json['limited'] as bool? ?? false,
      limitation: json['limitation'] as String?,
      multiInstance: json['multi_instance'] as bool? ?? false,
      configured: json['configured'] as bool? ?? false,
    );
  }
}

class MusicService {
  const MusicService({
    required this.instanceId,
    required this.domain,
    required this.name,
    this.status = 'loaded',
    this.available = false,
    this.enabled = true,
    this.account,
    this.error,
    this.limited = false,
    this.limitation,
    this.authKind = 'credentials',
    this.canReauthenticate = false,
    this.values = const {},
    this.entries = const [],
  });

  final String instanceId;
  final String domain;
  final String name;
  final String status;
  final bool available;
  final bool enabled;
  final String? account;
  final String? error;
  final bool limited;
  final String? limitation;
  final String authKind;
  final bool canReauthenticate;
  final Map<String, dynamic> values;
  final List<ConfigField> entries;

  factory MusicService.fromJson(Map<String, dynamic> json) {
    return MusicService(
      instanceId: json['instance_id'] as String,
      domain: json['domain'] as String,
      name: json['name'] as String? ?? json['domain'] as String,
      status: json['status'] as String? ?? 'loaded',
      available: json['available'] as bool? ?? false,
      enabled: json['enabled'] as bool? ?? true,
      account: json['account'] as String?,
      error: json['error'] as String?,
      limited: json['limited'] as bool? ?? false,
      limitation: json['limitation'] as String?,
      authKind: json['auth_kind'] as String? ?? 'credentials',
      canReauthenticate: json['can_reauthenticate'] as bool? ?? false,
      values: Map<String, dynamic>.from(json['values'] as Map? ?? const {}),
      entries: [
        for (final item in json['entries'] as List? ?? const [])
          ConfigField.fromJson(item as Map<String, dynamic>),
      ],
    );
  }

  String get statusLabel {
    switch (status) {
      case 'loaded':
        return available || status == 'loaded' ? 'Connected' : 'Connected';
      case 'loading':
        return 'Connecting';
      case 'needs_reauth':
        return 'Needs sign-in';
      case 'error':
        return 'Error';
      case 'disabled':
        return 'Off';
      case 'incompatible':
        return 'Not supported';
      default:
        return status;
    }
  }
}

class AuthSession {
  const AuthSession({
    required this.sessionId,
    required this.status,
    required this.domain,
    this.instanceId,
    this.flowId,
    this.authUrl,
    this.callbackScheme = 'freesounder',
    this.title,
    this.description,
    this.entries = const [],
    this.errors = const {},
    this.copyText,
    this.error,
  });

  final String sessionId;
  final String status;
  final String domain;
  final String? instanceId;
  final String? flowId;
  final String? authUrl;
  final String callbackScheme;
  final String? title;
  final String? description;
  final List<ConfigField> entries;
  final Map<String, String> errors;
  final String? copyText;
  final String? error;

  bool get isComplete => status == 'success' || status == 'error' || status == 'aborted';

  factory AuthSession.fromJson(Map<String, dynamic> json) {
    final errors = <String, String>{};
    final raw = json['errors'] as Map? ?? const {};
    raw.forEach((key, value) {
      errors[key.toString()] = value.toString();
    });
    return AuthSession(
      sessionId: json['session_id'] as String,
      status: json['status'] as String,
      domain: json['domain'] as String,
      instanceId: json['instance_id'] as String?,
      flowId: json['flow_id'] as String?,
      authUrl: json['auth_url'] as String?,
      callbackScheme: json['callback_scheme'] as String? ?? 'freesounder',
      title: json['title'] as String?,
      description: json['description'] as String?,
      entries: [
        for (final item in json['entries'] as List? ?? const [])
          ConfigField.fromJson(item as Map<String, dynamic>),
      ],
      errors: errors,
      copyText: json['copy_text'] as String?,
      error: json['error'] as String?,
    );
  }
}

class SpotifyConnectRoom {
  const SpotifyConnectRoom({required this.playerId, required this.name, required this.enabled});

  final String playerId;
  final String name;
  final bool enabled;

  factory SpotifyConnectRoom.fromJson(Map<String, dynamic> json) {
    return SpotifyConnectRoom(
      playerId: json['player_id'] as String,
      name: json['name'] as String? ?? json['player_id'] as String,
      enabled: json['enabled'] as bool? ?? false,
    );
  }
}

class SpotifyConnectStatus {
  const SpotifyConnectStatus({
    required this.available,
    this.configured = false,
    this.instanceId,
    this.engine,
    this.engineLabel,
    this.status,
    this.rooms = const [],
    this.note,
  });

  final bool available;
  final bool configured;
  final String? instanceId;
  final String? engine;
  final String? engineLabel;
  final String? status;
  final List<SpotifyConnectRoom> rooms;
  final String? note;

  factory SpotifyConnectStatus.fromJson(Map<String, dynamic> json) {
    return SpotifyConnectStatus(
      available: json['available'] as bool? ?? false,
      configured: json['configured'] as bool? ?? false,
      instanceId: json['instance_id'] as String?,
      engine: json['engine'] as String?,
      engineLabel: json['engine_label'] as String?,
      status: json['status'] as String?,
      rooms: [
        for (final item in json['rooms'] as List? ?? const [])
          SpotifyConnectRoom.fromJson(item as Map<String, dynamic>),
      ],
      note: json['note'] as String?,
    );
  }
}

class MusicServicesCatalog {
  const MusicServicesCatalog({this.services = const [], this.configured = const []});

  final List<MusicServiceType> services;
  final List<MusicService> configured;

  factory MusicServicesCatalog.fromJson(Map<String, dynamic> json) {
    return MusicServicesCatalog(
      services: [
        for (final item in json['services'] as List? ?? const [])
          MusicServiceType.fromJson(item as Map<String, dynamic>),
      ],
      configured: [
        for (final item in json['configured'] as List? ?? const [])
          MusicService.fromJson(item as Map<String, dynamic>),
      ],
    );
  }
}


const appleMusicLimitation =
    'Apple Music in Music Assistant is limited and experimental. Playback is not '
    'officially supported by Apple. Maximum quality is AAC 256 kbps; lossless and '
    'Atmos are not available. Sign-in tokens expire after about 180 days, and there '
    'can be gaps of up to five seconds between tracks.';
