import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/music_service.dart';
import '../models/now_playing.dart';
import '../models/player_settings.dart';
import '../models/room.dart';

/// HTTP + WebSocket client for services/control-api.
class ControlApiClient {
  ControlApiClient({
    required this.baseUrl,
    http.Client? httpClient,
    Duration requestTimeout = const Duration(seconds: 15),
  }) : _http = TimeoutClient(httpClient ?? http.Client(), requestTimeout);

  final String baseUrl;
  final http.Client _http;
  WebSocketChannel? _channel;

  Uri _uri(String path, [Map<String, String>? query]) {
    final root = baseUrl.endsWith('/') ? baseUrl.substring(0, baseUrl.length - 1) : baseUrl;
    return Uri.parse('$root$path').replace(queryParameters: query);
  }

  Future<SystemHealth> health() async {
    final response = await _http.get(_uri('/health'));
    _ensureOk(response);
    return SystemHealth.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<List<Room>> listRooms() async {
    final response = await _http.get(_uri('/api/v1/rooms'));
    _ensureOk(response);
    final data = jsonDecode(response.body) as List<dynamic>;
    return [for (final item in data) Room.fromJson(item as Map<String, dynamic>)];
  }

  Future<NowPlaying> nowPlaying(String playerId) async {
    final response = await _http.get(_uri('/api/v1/now-playing', {'player_id': playerId}));
    _ensureOk(response);
    return NowPlaying.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<void> play(String playerId) => _post('/api/v1/players/$playerId/play');

  Future<void> pause(String playerId) => _post('/api/v1/players/$playerId/pause');

  Future<void> next(String playerId) => _post('/api/v1/players/$playerId/next');

  Future<void> setVolume(String playerId, int level) async {
    final response = await _http.post(
      _uri('/api/v1/players/$playerId/volume'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'level': level}),
    );
    _ensureOk(response);
  }

  Future<void> joinGroup(String groupId, String playerId) async {
    final response = await _http.post(
      _uri('/api/v1/groups/$groupId/join'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'player_id': playerId}),
    );
    _ensureOk(response);
  }

  Future<void> leaveGroup(String groupId, String playerId) async {
    final response = await _http.post(
      _uri('/api/v1/groups/$groupId/leave'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'player_id': playerId}),
    );
    _ensureOk(response);
  }

  Future<List<UnclaimedPlayer>> listUnclaimed() async {
    final response = await _http.get(_uri('/api/v1/players/unclaimed'));
    _ensureOk(response);
    final data = jsonDecode(response.body) as List<dynamic>;
    return [for (final item in data) UnclaimedPlayer.fromJson(item as Map<String, dynamic>)];
  }

  /// Adopt a player through this system's host. Leave [role] null to let the
  /// host decide (its own board becomes the host, any other board a room).
  Future<UnclaimedPlayer> adoptPlayer({
    required String name,
    String? id,
    String? url,
    String? role,
  }) async {
    final response = await _http.post(
      _uri('/api/v1/players/adopt'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'name': name,
        'id': ?id,
        'url': ?url,
        'role': ?role,
      }),
    );
    _ensureOk(response);
    return UnclaimedPlayer.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<PlayerSettings> getSettings(String playerId) async {
    final response = await _http.get(_uri('/api/v1/players/$playerId/settings'));
    _ensureOk(response);
    return PlayerSettings.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<PlayerSettings> patchSettings(String playerId, Map<String, dynamic> patch) async {
    final response = await _http.patch(
      _uri('/api/v1/players/$playerId/settings'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(patch),
    );
    _ensureOk(response);
    return PlayerSettings.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<SshDeveloperStatus> getSsh(String playerId) async {
    final response = await _http.get(_uri('/api/v1/players/$playerId/developer/ssh'));
    _ensureOk(response);
    return SshDeveloperStatus.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<SshDeveloperStatus> setSsh(
    String playerId, {
    required bool enabled,
    bool confirm = false,
    String? publicKey,
  }) async {
    final response = await _http.post(
      _uri('/api/v1/players/$playerId/developer/ssh'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'enabled': enabled,
        'confirm': confirm,
        if (publicKey != null && publicKey.isNotEmpty) 'public_key': publicKey,
      }),
    );
    _ensureOk(response);
    return SshDeveloperStatus.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<MusicServicesCatalog> musicServicesCatalog() async {
    final response = await _http.get(_uri('/api/v1/music-services/catalog'));
    _ensureOk(response);
    return MusicServicesCatalog.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<List<MusicService>> listMusicServices() async {
    final response = await _http.get(_uri('/api/v1/music-services'));
    _ensureOk(response);
    final data = jsonDecode(response.body) as List<dynamic>;
    return [for (final item in data) MusicService.fromJson(item as Map<String, dynamic>)];
  }

  Future<MusicService> getMusicService(String instanceId) async {
    final response = await _http.get(_uri('/api/v1/music-services/$instanceId'));
    _ensureOk(response);
    return MusicService.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> addMusicService(String domain, {Map<String, dynamic> values = const {}}) async {
    final response = await _http.post(
      _uri('/api/v1/music-services'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'domain': domain, 'values': values}),
    );
    _ensureOk(response);
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<MusicService> updateMusicService(String instanceId, Map<String, dynamic> values) async {
    final response = await _http.patch(
      _uri('/api/v1/music-services/$instanceId'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'values': values}),
    );
    _ensureOk(response);
    return MusicService.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<void> removeMusicService(String instanceId) async {
    final response = await _http.delete(_uri('/api/v1/music-services/$instanceId'));
    _ensureOk(response);
  }

  Future<AuthSession> startMusicServiceAuth({String? domain, String? instanceId}) async {
    final response = await _http.post(
      _uri('/api/v1/music-services/auth'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        if (domain != null) 'domain': domain,
        if (instanceId != null) 'instance_id': instanceId,
      }),
    );
    _ensureOk(response);
    return AuthSession.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<AuthSession> getMusicServiceAuth(String sessionId) async {
    final response = await _http.get(_uri('/api/v1/music-services/auth/$sessionId'));
    _ensureOk(response);
    return AuthSession.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<AuthSession> submitMusicServiceAuth(String sessionId, Map<String, dynamic> values) async {
    final response = await _http.post(
      _uri('/api/v1/music-services/auth/$sessionId/submit'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'values': values}),
    );
    _ensureOk(response);
    return AuthSession.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<AuthSession> abortMusicServiceAuth(String sessionId) async {
    final response = await _http.post(_uri('/api/v1/music-services/auth/$sessionId/abort'));
    _ensureOk(response);
    return AuthSession.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<SpotifyConnectStatus> spotifyConnectStatus() async {
    final response = await _http.get(_uri('/api/v1/music-services/spotify-connect'));
    _ensureOk(response);
    return SpotifyConnectStatus.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<SpotifyConnectStatus> setSpotifyConnect(String playerId, bool enabled) async {
    final response = await _http.post(
      _uri('/api/v1/music-services/spotify-connect/$playerId'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'enabled': enabled}),
    );
    _ensureOk(response);
    return SpotifyConnectStatus.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Stream<Map<String, dynamic>> connectEvents() {
    final httpUri = Uri.parse(baseUrl);
    final scheme = httpUri.scheme == 'https' ? 'wss' : 'ws';
    final wsUri = httpUri.replace(scheme: scheme, path: '/api/v1/ws');
    _channel = WebSocketChannel.connect(wsUri);
    return _channel!.stream.map((raw) {
      if (raw is String) {
        return jsonDecode(raw) as Map<String, dynamic>;
      }
      return {'type': 'event', 'event': 'unknown', 'data': raw};
    });
  }

  Future<void> close() async {
    await _channel?.sink.close();
    _http.close();
  }

  Future<void> _post(String path) async {
    final response = await _http.post(_uri(path));
    _ensureOk(response);
  }

  void _ensureOk(http.Response response) => ensureOk(response);
}

/// Throw a [ControlApiException] carrying the status and the server's detail.
void ensureOk(http.Response response) {
  if (response.statusCode >= 200 && response.statusCode < 300) {
    return;
  }
  String? detail;
  String? maState;
  String? maDetail;
  try {
    final data = jsonDecode(response.body);
    if (data is Map<String, dynamic>) {
      final raw = data['detail'];
      detail = raw is String ? raw : (raw == null ? null : jsonEncode(raw));
      maState = data['ma_state'] as String?;
      maDetail = data['ma_detail'] as String?;
    }
  } on FormatException {
    detail = null;
  }
  final body = response.body.length > 300 ? '${response.body.substring(0, 300)}…' : response.body;
  throw ControlApiException(
    'HTTP ${response.statusCode}: ${detail ?? body}',
    statusCode: response.statusCode,
    detail: detail,
    maState: maState,
    maDetail: maDetail,
    url: response.request?.url.toString(),
  );
}

class ControlApiException implements Exception {
  ControlApiException(
    this.message, {
    this.statusCode,
    this.detail,
    this.maState,
    this.maDetail,
    this.url,
  });
  final String message;
  final int? statusCode;
  final String? detail;

  /// Set when the system answered 503 because Music Assistant is not ready.
  final String? maState;
  final String? maDetail;
  final String? url;

  bool get systemStarting => statusCode == 503 || maState != null;

  @override
  String toString() => 'ControlApiException: $message';
}

/// /health from the control API. Older players omit the ma_state fields.
class SystemHealth {
  const SystemHealth({
    required this.ok,
    required this.maConnected,
    required this.maState,
    this.maDetail,
    this.version,
  });

  final bool ok;
  final bool maConnected;
  final String maState;
  final String? maDetail;
  final String? version;

  factory SystemHealth.fromJson(Map<String, dynamic> json) {
    final connected = json['ma_connected'] as bool? ?? false;
    return SystemHealth(
      ok: json['status'] == 'ok',
      maConnected: connected,
      maState: json['ma_state'] as String? ?? (connected ? 'connected' : 'unavailable'),
      maDetail: json['ma_detail'] as String?,
      version: json['version'] as String?,
    );
  }
}

/// Every request gets a deadline, so a player that stops answering mid-request
/// (for example while it restarts a service) can never hang the app.
class TimeoutClient extends http.BaseClient {
  TimeoutClient(this._inner, this.timeout);

  final http.Client _inner;
  final Duration timeout;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) {
    return _inner.send(request).timeout(
          timeout,
          onTimeout: () => throw TimeoutException(
            '${request.method} ${request.url} did not answer within ${timeout.inSeconds}s',
            timeout,
          ),
        );
  }

  @override
  void close() => _inner.close();
}
