import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'spotify_auth.dart';
import 'spotify_models.dart';

class SpotifyApiException implements Exception {
  SpotifyApiException(this.statusCode, this.message, {this.reason});

  final int statusCode;
  final String message;

  /// Spotify's player error reason, e.g. PREMIUM_REQUIRED or NO_ACTIVE_DEVICE.
  final String? reason;

  bool get premiumRequired => reason == 'PREMIUM_REQUIRED' || message.toLowerCase().contains('premium');
  bool get notAllowlisted => statusCode == 403 && message.toLowerCase().contains('user may not be registered');

  String get friendly {
    if (premiumRequired) {
      return 'Spotify Premium is needed to play music on your speakers.';
    }
    if (notAllowlisted) {
      return 'This Spotify account is not on your developer app\'s user list. In the Spotify '
          'dashboard open your app, then User Management, and add this account\'s email.';
    }
    if (statusCode == 429) {
      return 'Spotify is busy (rate limit). Try again in a minute.';
    }
    return message;
  }

  @override
  String toString() => friendly;
}

/// The parts of the Spotify Web API that development-mode apps may still use
/// (checked against Spotify's February 2026 changes): no recommendations,
/// featured playlists, browse categories, new releases or artist top tracks.
class SpotifyApi {
  SpotifyApi(this.auth, {http.Client? httpClient}) : _http = httpClient ?? http.Client();

  final SpotifyAuth auth;
  final http.Client _http;
  Map<String, dynamic>? _me;

  static const _base = 'https://api.spotify.com/v1';

  Future<dynamic> _request(String method, String path, {Map<String, String>? query, Object? body}) async {
    var forceRefresh = false;
    for (var attempt = 0; attempt < 3; attempt++) {
      final token = await auth.accessToken(forceRefresh: forceRefresh);
      final uri = Uri.parse('$_base$path').replace(queryParameters: query);
      final request = http.Request(method, uri)..headers['Authorization'] = 'Bearer $token';
      if (body != null) {
        request.headers['Content-Type'] = 'application/json';
        request.body = jsonEncode(body);
      } else if (method != 'GET') {
        request.headers['Content-Length'] = '0';
      }
      final response = await http.Response.fromStream(
        await _http.send(request).timeout(const Duration(seconds: 15)),
      );
      if (response.statusCode == 401 && attempt == 0) {
        forceRefresh = true;
        continue;
      }
      if (response.statusCode == 429 && attempt < 2) {
        final wait = int.tryParse(response.headers['retry-after'] ?? '') ?? 2;
        if (wait <= 5) {
          await Future<void>.delayed(Duration(seconds: wait));
          continue;
        }
      }
      if (response.statusCode >= 400) {
        throw _error(response);
      }
      if (response.body.isEmpty) {
        return null;
      }
      try {
        return jsonDecode(response.body);
      } on FormatException {
        return null;
      }
    }
    throw SpotifyApiException(429, 'Spotify is busy (rate limit). Try again in a minute.');
  }

  static SpotifyApiException _error(http.Response response) {
    var message = 'Spotify answered HTTP ${response.statusCode}';
    String? reason;
    try {
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final error = data['error'];
      if (error is Map) {
        message = error['message'] as String? ?? message;
        reason = error['reason'] as String?;
      } else if (error is String) {
        message = data['error_description'] as String? ?? error;
      }
    } on Object {
      if (response.body.isNotEmpty && response.body.length < 200) {
        message = response.body;
      }
    }
    return SpotifyApiException(response.statusCode, message, reason: reason);
  }

  Future<Map<String, dynamic>> _getMap(String path, [Map<String, String>? query]) async =>
      (await _request('GET', path, query: query) as Map<String, dynamic>?) ?? const {};

  // ------------------------------------------------------------------ user
  Future<Map<String, dynamic>> me({bool refresh = false}) async {
    if (_me != null && !refresh) {
      return _me!;
    }
    return _me = await _getMap('/me');
  }

  /// The Spotify username (the id go-librespot signs in with).
  Future<String> username() async => (await me())['id'] as String;

  void forgetUser() => _me = null;

  // ------------------------------------------------------------------ home
  Future<List<SpotifyItem>> recentlyPlayed() async {
    final data = await _getMap('/me/player/recently-played', {'limit': '30'});
    final seen = <String>{};
    final out = <SpotifyItem>[];
    for (final entry in data['items'] as List? ?? const []) {
      final item = SpotifyItem.fromJson((entry as Map)['track']);
      if (item != null && seen.add(item.uri)) {
        final context = entry['context'];
        out.add(item.inContext(context is Map ? context['uri'] as String? : null));
      }
    }
    return out;
  }

  Future<List<SpotifyItem>> _list(String path, {String? key, Map<String, String>? query}) async {
    final data = await _getMap(path, query ?? {'limit': '50'});
    final items = data['items'] as List? ?? const [];
    return [
      for (final entry in items)
        ?SpotifyItem.fromJson(key == null ? entry : (entry as Map)[key]),
    ];
  }

  Future<List<SpotifyItem>> myPlaylists() => _list('/me/playlists');

  Future<List<SpotifyItem>> savedAlbums() => _list('/me/albums', key: 'album');

  Future<List<SpotifyItem>> savedShows() => _list('/me/shows', key: 'show');

  Future<List<SpotifyItem>> followedArtists() async {
    final data = await _getMap('/me/following', {'type': 'artist', 'limit': '50'});
    final artists = data['artists'];
    return [
      for (final a in (artists is Map ? artists['items'] as List? : null) ?? const []) ?SpotifyItem.fromJson(a),
    ];
  }

  Future<List<SpotifyItem>> topTracks() => _list('/me/top/tracks', query: {'limit': '20', 'time_range': 'short_term'});

  // ------------------------------------------------------------------ search
  /// Development mode returns at most 10 per type.
  Future<List<SpotifySection>> search(String query) async {
    final data = await _getMap('/search', {'q': query, 'type': 'track,artist,album,playlist', 'limit': '10'});
    List<SpotifyItem> items(String key) {
      final block = data[key];
      return [for (final i in (block is Map ? block['items'] as List? : null) ?? const []) ?SpotifyItem.fromJson(i)];
    }

    return [
      SpotifySection('Songs', items('tracks')),
      SpotifySection('Artists', items('artists')),
      SpotifySection('Albums', items('albums')),
      SpotifySection('Playlists', items('playlists')),
    ].where((s) => s.items.isNotEmpty).toList();
  }

  // ------------------------------------------------------------------ pages
  Future<SpotifyPage> album(String id) async {
    final data = await _getMap('/albums/$id');
    final item = SpotifyItem.fromJson(data)!;
    final images = data['images'];
    final tracks = (data['tracks'] as Map?)?['items'] as List? ?? const [];
    return SpotifyPage(
      item: item,
      imageUrl: SpotifyItem.bestImage(images),
      tracks: [
        for (final t in tracks) ?SpotifyItem.fromJson(t, fallbackImages: images)?.inContext(item.uri),
      ],
    );
  }

  /// Development mode only lists the items of playlists the user owns or
  /// collaborates on; for others the page has no tracks and a note.
  Future<SpotifyPage> playlist(String id) async {
    final data = await _getMap('/playlists/$id');
    final item = SpotifyItem.fromJson(data)!;
    final block = data['items'] ?? data['tracks'];
    final entries = block is Map ? block['items'] as List? : null;
    final tracks = <SpotifyItem>[
      for (final e in entries ?? const [])
        ?SpotifyItem.fromJson((e as Map)['item'] ?? e['track'])?.inContext(item.uri),
    ];
    return SpotifyPage(
      item: item,
      imageUrl: SpotifyItem.bestImage(data['images']),
      tracks: tracks,
      note: entries == null ? 'Spotify only lists songs of playlists you own here. You can still play it.' : null,
    );
  }

  Future<SpotifyPage> artist(String id) async {
    final data = await _getMap('/artists/$id');
    final item = SpotifyItem.fromJson(data)!;
    final albums = await _list('/artists/$id/albums', query: {'include_groups': 'album,single', 'limit': '10'});
    return SpotifyPage(
      item: item,
      imageUrl: SpotifyItem.bestImage(data['images']),
      sections: [if (albums.isNotEmpty) SpotifySection('Albums and singles', albums)],
    );
  }

  Future<SpotifyPage> show(String id) async {
    final data = await _getMap('/shows/$id');
    final item = SpotifyItem.fromJson(data)!;
    final images = data['images'];
    final episodes = await _getMap('/shows/$id/episodes', {'limit': '20'});
    return SpotifyPage(
      item: item,
      imageUrl: SpotifyItem.bestImage(images),
      tracks: [
        for (final e in episodes['items'] as List? ?? const [])
          ?SpotifyItem.fromJson(e, fallbackImages: images)?.inContext(item.uri),
      ],
    );
  }

  // ------------------------------------------------------------------ player
  Future<List<Map<String, dynamic>>> devices() async {
    final data = await _getMap('/me/player/devices');
    return [for (final d in data['devices'] as List? ?? const []) d as Map<String, dynamic>];
  }

  /// Start [contextUri] (optionally at [offsetUri]) or [uris] on [deviceId].
  Future<void> play(String deviceId, {String? contextUri, List<String>? uris, String? offsetUri}) async {
    final body = <String, dynamic>{
      'context_uri': ?contextUri,
      'uris': ?uris,
      if (offsetUri != null && contextUri != null) 'offset': {'uri': offsetUri},
    };
    try {
      await _request('PUT', '/me/player/play', query: {'device_id': deviceId}, body: body);
    } on SpotifyApiException catch (err) {
      if (err.statusCode != 404) {
        rethrow;
      }
      await transfer(deviceId);
      await _request('PUT', '/me/player/play', query: {'device_id': deviceId}, body: body);
    }
  }

  Future<void> transfer(String deviceId, {bool play = false}) =>
      _request('PUT', '/me/player', body: {'device_ids': [deviceId], 'play': play});

  Future<void> pause(String deviceId) => _request('PUT', '/me/player/pause', query: {'device_id': deviceId});

  Future<void> resume(String deviceId) => _request('PUT', '/me/player/play', query: {'device_id': deviceId});

  Future<void> next(String deviceId) => _request('POST', '/me/player/next', query: {'device_id': deviceId});

  Future<void> previous(String deviceId) => _request('POST', '/me/player/previous', query: {'device_id': deviceId});

  Future<void> seek(String deviceId, int positionMs) =>
      _request('PUT', '/me/player/seek', query: {'device_id': deviceId, 'position_ms': '$positionMs'});

  Future<void> volume(String deviceId, int percent) => _request(
        'PUT',
        '/me/player/volume',
        query: {'device_id': deviceId, 'volume_percent': '${percent.clamp(0, 100)}'},
      );
}
