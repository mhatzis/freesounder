import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'control_client.dart';

/// Which part of adding a player failed, with the precise reason.
class AdoptFailure implements Exception {
  AdoptFailure(this.step, this.reason, {this.statusCode, this.url});

  /// Human step name, e.g. "Creating your system".
  final String step;

  /// What went wrong: the player's HTTP detail or the network exception.
  final String reason;
  final int? statusCode;
  final String? url;

  /// One short line for the sheet.
  String get message {
    final code = statusCode == null ? '' : ' (HTTP $statusCode)';
    return 'Could not add the player: $step failed$code. $hint';
  }

  String get hint {
    if (statusCode == 503) {
      return 'Your system is still starting. Wait a minute and try again.';
    }
    if (statusCode == null) {
      return 'Check your phone is on the same Wi-Fi as the player, and that FreeSounder may use the local network (Settings › Privacy › Local Network).';
    }
    return 'Try again.';
  }

  /// Developer-mode line: step, URL, status and the raw reason.
  String get debugLine =>
      'adopt: $step${url == null ? '' : ' $url'}${statusCode == null ? '' : ' -> HTTP $statusCode'}: $reason';

  @override
  String toString() => 'AdoptFailure($debugLine)';
}

typedef AdoptProgress = void Function(String message);

/// Set up the first player of a new home (no player connected yet).
///
/// Talks to the board itself by IP (from the Improv redirect), not mDNS: iOS
/// multicast needs an entitlement and the board renames itself to fs-ROOM.
/// The first request also triggers iOS's local-network permission prompt, so
/// every step retries for a while instead of failing on the first refusal.
class FirstPlayerSetup {
  FirstPlayerSetup({
    http.Client? httpClient,
    this.apiPort = 8080,
    this.reachTimeout = const Duration(seconds: 90),
    this.retryDelay = const Duration(seconds: 2),
    this.requestTimeout = const Duration(seconds: 8),
    this.adoptTimeout = const Duration(seconds: 30),
  }) : _http = httpClient ?? http.Client();

  final http.Client _http;
  final int apiPort;
  final Duration reachTimeout;
  final Duration retryDelay;
  final Duration requestTimeout;
  final Duration adoptTimeout;

  /// Name the room on the board at [ip]; the board creates the household.
  /// Returns the player's API base URL.
  Future<String> createSystem({
    required String ip,
    required String name,
    AdoptProgress? onProgress,
  }) async {
    final api = 'http://$ip:$apiPort';

    onProgress?.call('Reaching the player at $ip…');
    final info = await _retryJson('Reaching the player', Uri.parse('$api/api/v2/info'));
    final playerId = info['id'] as String?;
    if (playerId == null) {
      throw AdoptFailure('Reaching the player', 'The player did not report an id');
    }
    if (info['state'] == 'ready') {
      // Already set up (for example a retry after the reply was lost).
      return api;
    }

    onProgress?.call('Naming $name…');
    final setupUri = Uri.parse('$api/api/v2/setup');
    try {
      final response = await _http
          .post(
            setupUri,
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'room': name}),
          )
          .timeout(adoptTimeout);
      _check('Setting up the room', setupUri, response);
    } on AdoptFailure {
      rethrow;
    } on Object catch (err) {
      throw AdoptFailure('Setting up the room', _describe(err), url: setupUri.toString());
    }
    await _retryJson('Connecting to the player', Uri.parse('$api/health'));
    return api;
  }

  Future<Map<String, dynamic>> _retryJson(String step, Uri uri) async {
    final deadline = DateTime.now().add(reachTimeout);
    AdoptFailure? last;
    while (true) {
      try {
        final response = await _http.get(uri).timeout(requestTimeout);
        _check(step, uri, response);
        final data = jsonDecode(response.body);
        if (data is Map<String, dynamic>) {
          return data;
        }
        throw AdoptFailure(step, 'Unexpected answer: ${_short(response.body)}', url: uri.toString());
      } on AdoptFailure catch (err) {
        last = err;
      } on Object catch (err) {
        last = AdoptFailure(step, _describe(err), url: uri.toString());
      }
      if (DateTime.now().isAfter(deadline)) {
        throw last;
      }
      await Future<void>.delayed(retryDelay);
    }
  }

  void _check(String step, Uri uri, http.Response response) {
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return;
    }
    var reason = _short(response.body);
    try {
      final data = jsonDecode(response.body);
      if (data is Map<String, dynamic> && data['detail'] != null) {
        reason = data['detail'].toString();
      }
    } on FormatException {
      // keep the raw body
    }
    throw AdoptFailure(step, reason, statusCode: response.statusCode, url: uri.toString());
  }

  void close() => _http.close();
}

/// Add a board to the home the app is connected to: the connected player
/// hands the new one the household, then the new room shows up in the list.
Future<void> joinExistingSystem(
  ControlApiClient client, {
  required String name,
  String? id,
  String? url,
  String? ip,
}) async {
  try {
    await client.adoptPlayer(name: name, id: id, url: url, ip: ip);
  } on ControlApiException catch (err) {
    throw AdoptFailure(
      'Adding it to your home',
      err.detail ?? err.message,
      statusCode: err.statusCode,
      url: err.url ?? '${client.baseUrl}/api/v2/players/adopt',
    );
  } on Object catch (err) {
    throw AdoptFailure(
      'Adding it to your home',
      _describe(err),
      url: '${client.baseUrl}/api/v2/players/adopt',
    );
  }
}

String _describe(Object err) {
  if (err is TimeoutException) {
    return 'No answer within ${err.duration?.inSeconds ?? '?'}s';
  }
  final text = err.toString();
  return text.length > 240 ? '${text.substring(0, 240)}…' : text;
}

String _short(String body) => body.length > 200 ? '${body.substring(0, 200)}…' : body;

/// The provisioned board's IP: Improv result IP, else the redirect URL host.
String? provisionedIp({String? ip, String? redirect, String? url}) {
  for (final candidate in [ip, redirect, url]) {
    if (candidate == null || candidate.isEmpty) {
      continue;
    }
    final parsed = Uri.tryParse(candidate.contains('://') ? candidate : 'http://$candidate');
    final host = parsed?.host;
    if (host != null && host.isNotEmpty) {
      return host;
    }
  }
  return null;
}
