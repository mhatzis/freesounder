import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/now_playing.dart';
import '../models/player_settings.dart';
import '../models/room.dart';

/// HTTP + WebSocket client for services/control-api.
class ControlApiClient {
  ControlApiClient({required this.baseUrl, http.Client? httpClient})
    : _http = httpClient ?? http.Client();

  final String baseUrl;
  final http.Client _http;
  WebSocketChannel? _channel;

  Uri _uri(String path, [Map<String, String>? query]) {
    final root = baseUrl.endsWith('/') ? baseUrl.substring(0, baseUrl.length - 1) : baseUrl;
    return Uri.parse('$root$path').replace(queryParameters: query);
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

  Future<UnclaimedPlayer> adoptPlayer({required String name, String? id, String? url}) async {
    final response = await _http.post(
      _uri('/api/v1/players/adopt'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'name': name,
        'id': ?id,
        'url': ?url,
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

  void _ensureOk(http.Response response) {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ControlApiException('HTTP ${response.statusCode}: ${response.body}');
    }
  }
}

class ControlApiException implements Exception {
  ControlApiException(this.message);
  final String message;

  @override
  String toString() => 'ControlApiException: $message';
}
