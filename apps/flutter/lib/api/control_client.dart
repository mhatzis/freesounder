import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/now_playing.dart';
import '../models/player_settings.dart';
import '../models/radio_station.dart';
import '../models/room.dart';

/// HTTP + WebSocket client for a player's freesounderd v2 API.
///
/// Every player is equal: the app talks to any one of them and that player
/// forwards room or group requests to the right player (the group leader for
/// playback, the room's own player for its volume and settings). Room ids work
/// anywhere a group id is expected; the player resolves the room's group.
class ControlApiClient {
  ControlApiClient({
    required this.baseUrl,
    http.Client? httpClient,
    Duration requestTimeout = const Duration(seconds: 15),
  }) : _http = TimeoutClient(httpClient ?? http.Client(), requestTimeout);

  final String baseUrl;
  final http.Client _http;
  WebSocketChannel? _channel;

  static const _json = {'Content-Type': 'application/json'};

  Uri _uri(String path, [Map<String, String>? query]) {
    final root = baseUrl.endsWith('/') ? baseUrl.substring(0, baseUrl.length - 1) : baseUrl;
    return Uri.parse('$root$path').replace(queryParameters: query);
  }

  Future<dynamic> _getJson(String path, [Map<String, String>? query]) async {
    final response = await _http.get(_uri(path, query));
    _ensureOk(response);
    return jsonDecode(response.body);
  }

  Future<dynamic> _send(String method, String path, [Object? body, bool slow = false]) async {
    final request = http.Request(method, _uri(path))
      ..headers.addAll(_json)
      ..headers.addAll(slow ? const {TimeoutClient.slowHeader: '1'} : const {})
      ..body = jsonEncode(body ?? const <String, dynamic>{});
    final response = await http.Response.fromStream(await _http.send(request));
    _ensureOk(response);
    return response.body.isEmpty ? null : jsonDecode(response.body);
  }

  Future<SystemHealth> health() async {
    return SystemHealth.fromJson(await _getJson('/health') as Map<String, dynamic>);
  }

  /// This player's identity (`state` is `unclaimed` or `ready`).
  Future<Map<String, dynamic>> info() async => await _getJson('/api/v2/info') as Map<String, dynamic>;

  Future<List<Room>> listRooms() async {
    final data = await _getJson('/api/v2/rooms') as List<dynamic>;
    return [for (final item in data) Room.fromJson(item as Map<String, dynamic>)];
  }

  Future<NowPlaying> nowPlaying(String playerId) async {
    final data = await _getJson('/api/v2/now-playing', {'player_id': playerId});
    return NowPlaying.fromJson(data as Map<String, dynamic>);
  }

  Future<void> play(String roomOrGroupId) => _send('POST', '/api/v2/groups/$roomOrGroupId/play');

  Future<void> pause(String roomOrGroupId) => _send('POST', '/api/v2/groups/$roomOrGroupId/pause');

  Future<void> next(String roomOrGroupId) => _send('POST', '/api/v2/groups/$roomOrGroupId/next');

  Future<void> previous(String roomOrGroupId) =>
      _send('POST', '/api/v2/groups/$roomOrGroupId/previous');

  Future<void> stop(String roomOrGroupId) => _send('POST', '/api/v2/groups/$roomOrGroupId/stop');

  /// One room's volume (0-100).
  Future<void> setVolume(String playerId, int level) =>
      _send('POST', '/api/v2/rooms/$playerId/volume', {'volume': level});

  Future<void> setMuted(String playerId, bool muted) =>
      _send('POST', '/api/v2/rooms/$playerId/volume', {'muted': muted});

  /// Group volume: moves every room in the group by the same amount.
  Future<void> setGroupVolume(String groupId, int level) =>
      _send('POST', '/api/v2/groups/$groupId/volume', {'volume': level});

  /// Put [playerId] into [groupId] (a group id or any room id in that group).
  Future<void> joinGroup(String groupId, String playerId) =>
      _send('POST', '/api/v2/rooms/$playerId/join', {'group': groupId});

  /// Take [playerId] out of its group. A playing leader keeps serving the
  /// others ("relay mode") until the music stops.
  Future<void> leaveGroup(String groupId, String playerId) =>
      _send('POST', '/api/v2/rooms/$playerId/leave');

  Future<void> setGroupMembers(String groupId, List<String> memberIds) =>
      _send('PUT', '/api/v2/groups/$groupId/members', {'members': memberIds});

  Future<List<RadioStation>> searchRadio({String? query, String? country, String? tag}) async {
    final data = await _getJson('/api/v2/radio/search', {
      if (query != null && query.isNotEmpty) 'q': query,
      'country': ?country,
      'tag': ?tag,
    }) as List<dynamic>;
    return [for (final item in data) RadioStation.fromJson(item as Map<String, dynamic>)];
  }

  Future<List<RadioStation>> recentRadio() async {
    final data = await _getJson('/api/v2/radio/favourites') as List<dynamic>;
    return [for (final item in data) RadioStation.fromJson(item as Map<String, dynamic>)];
  }

  Future<void> playRadio(String roomOrGroupId, RadioStation station) =>
      _send('POST', '/api/v2/groups/$roomOrGroupId/radio', station.toPlayJson());

  /// Seek the group's Spotify playback (milliseconds).
  /// Plays left / right / both tones through this room's full audio chain.
  Future<Map<String, dynamic>> testTone(String playerId) async {
    final data = await _send('POST', '/api/v2/rooms/$playerId/test-tone', null, true);
    return (data as Map?)?.cast<String, dynamic>() ?? const {};
  }

  /// Active output device, EQ chain self-test, snapclient and stream state.
  Future<Map<String, dynamic>> roomAudio(String playerId) async {
    final data = await _send('GET', '/api/v2/rooms/$playerId/audio');
    return (data as Map?)?.cast<String, dynamic>() ?? const {};
  }

  Future<void> seek(String roomOrGroupId, int positionMs) =>
      _send('POST', '/api/v2/groups/$roomOrGroupId/seek', {'position_ms': positionMs});

  /// The group leader's Spotify Connect device and sign-in state.
  Future<PlayerSpotify> spotifyInfo(String roomOrGroupId) async {
    final data = await _send('POST', '/api/v2/groups/$roomOrGroupId/spotify');
    return PlayerSpotify.fromJson(data as Map<String, dynamic>);
  }

  /// Sign the group's Spotify device in with the app's token (used once; the
  /// player keeps go-librespot's own stored credentials, not the token).
  Future<PlayerSpotify> spotifyLogin(String roomOrGroupId, {required String username, required String accessToken}) async {
    final data = await _send(
      'POST',
      '/api/v2/groups/$roomOrGroupId/spotify/login',
      {'username': username, 'access_token': accessToken},
      true,
    );
    return PlayerSpotify.fromJson(data as Map<String, dynamic>);
  }

  /// Play a Spotify URI (album, playlist, artist, show or track) on the group.
  /// [skipToUri] starts a context at that track.
  Future<void> spotifyPlay(String roomOrGroupId, String uri, {String? skipToUri}) =>
      _send('POST', '/api/v2/groups/$roomOrGroupId/spotify/play', {'uri': uri, 'skip_to_uri': ?skipToUri});

  /// Track list of a context from go-librespot (works for playlists the Web
  /// API won't list in development mode).
  Future<List<Map<String, dynamic>>> spotifyContextTracks(String roomOrGroupId, String uri) async {
    final data = await _send('POST', '/api/v2/groups/$roomOrGroupId/spotify/context-tracks', {'uri': uri});
    final tracks = (data as Map<String, dynamic>)['tracks'] as List? ?? const [];
    return [for (final t in tracks) t as Map<String, dynamic>];
  }

  Future<List<UnclaimedPlayer>> listUnclaimed() async {
    final data = await _getJson('/api/v2/players/unclaimed') as List<dynamic>;
    return [for (final item in data) UnclaimedPlayer.fromJson(item as Map<String, dynamic>)];
  }

  /// Ask this (set up) player to set up a new player into the household.
  /// The household key goes player to player; the app never sees it.
  Future<Map<String, dynamic>> adoptPlayer({
    required String name,
    String? id,
    String? url,
    String? role,
    String? ip,
  }) async {
    // The new player always serves the v2 API on :8080, whatever port the
    // Improv redirect named, so send its address rather than the URL.
    final host = ip ?? (url == null ? null : Uri.tryParse(url.contains('://') ? url : 'http://$url')?.host);
    final body = (host != null && host.isNotEmpty) ? {'room': name, 'ip': host} : {'room': name, 'url': ?url};
    final data = await _send('POST', '/api/v2/players/adopt', body);
    return data as Map<String, dynamic>;
  }

  Future<PlayerSettings> getSettings(String playerId) async {
    final data = await _getJson('/api/v2/rooms/$playerId/settings');
    return PlayerSettings.fromJson(data as Map<String, dynamic>);
  }

  Future<PlayerSettings> patchSettings(String playerId, Map<String, dynamic> patch) async {
    final data = await _send('PATCH', '/api/v2/rooms/$playerId/settings', patch);
    return PlayerSettings.fromJson(data as Map<String, dynamic>);
  }

  Future<SshDeveloperStatus> getSsh(String playerId) async {
    final data = await _getJson('/api/v2/rooms/$playerId/developer/ssh');
    return SshDeveloperStatus.fromJson(data as Map<String, dynamic>);
  }

  Future<SshDeveloperStatus> setSsh(
    String playerId, {
    required bool enabled,
    bool confirm = false,
    String? publicKey,
  }) async {
    final data = await _send('POST', '/api/v2/rooms/$playerId/developer/ssh', {
      'enabled': enabled,
      'confirm': confirm,
      if (publicKey != null && publicKey.isNotEmpty) 'public_key': publicKey,
    });
    return SshDeveloperStatus.fromJson(data as Map<String, dynamic>);
  }

  Stream<Map<String, dynamic>> connectEvents() {
    final httpUri = Uri.parse(baseUrl);
    final scheme = httpUri.scheme == 'https' ? 'wss' : 'ws';
    final wsUri = httpUri.replace(scheme: scheme, path: '/api/v2/ws');
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

  /// Set by pre-v2 systems that answered 503 while Music Assistant started.
  final String? maState;
  final String? maDetail;
  final String? url;

  bool get systemStarting => statusCode == 503 || maState != null;

  @override
  String toString() => 'ControlApiException: $message';
}

/// /health from a player. `state` is `unclaimed` or `ready`.
class SystemHealth {
  const SystemHealth({
    required this.ok,
    this.state,
    this.maState,
    this.maDetail,
    this.version,
    this.api,
  });

  final bool ok;
  final String? state;

  /// Only set by pre-v2 (Music Assistant) systems.
  final String? maState;
  final String? maDetail;
  final String? version;
  final String? api;

  bool get maConnected => maState == null || maState == 'connected';

  factory SystemHealth.fromJson(Map<String, dynamic> json) {
    return SystemHealth(
      ok: json['status'] == 'ok',
      state: json['state'] as String?,
      maState: json['ma_state'] as String?,
      maDetail: json['ma_detail'] as String?,
      version: json['version'] as String?,
      api: json['api'] as String?,
    );
  }
}

/// Every request gets a deadline, so a player that stops answering mid-request
/// (for example while it restarts a service) can never hang the app.
class TimeoutClient extends http.BaseClient {
  TimeoutClient(this._inner, this.timeout);

  /// Request header (stripped before sending) for calls that may take a minute.
  static const slowHeader = 'x-fs-slow';

  final http.Client _inner;
  final Duration timeout;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) {
    final slow = request.headers.remove(slowHeader) != null;
    final limit = slow ? const Duration(seconds: 90) : timeout;
    return _inner.send(request).timeout(
          limit,
          onTimeout: () => throw TimeoutException(
            '${request.method} ${request.url} did not answer within ${limit.inSeconds}s',
            limit,
          ),
        );
  }

  @override
  void close() => _inner.close();
}

/// A group's Spotify Connect device as its leader reports it.
class PlayerSpotify {
  const PlayerSpotify({
    required this.leaderId,
    this.deviceId,
    this.deviceName,
    this.loggedIn = false,
    this.username,
  });

  final String leaderId;
  final String? deviceId;
  final String? deviceName;
  final bool loggedIn;
  final String? username;

  factory PlayerSpotify.fromJson(Map<String, dynamic> json) => PlayerSpotify(
        leaderId: json['leader_id'] as String? ?? '',
        deviceId: json['device_id'] as String?,
        deviceName: json['device_name'] as String?,
        loggedIn: json['logged_in'] as bool? ?? false,
        username: json['username'] as String?,
      );
}
