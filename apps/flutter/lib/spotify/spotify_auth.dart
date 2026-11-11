import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_web_auth_2/flutter_web_auth_2.dart';
import 'package:http/http.dart' as http;

import 'loopback.dart';

/// Spotify sign-in: Authorization Code + PKCE, no client secret anywhere.
///
/// Spotify development-mode apps allow only a handful of users each, so every
/// household can bring its own (free) Spotify developer app: the user pastes
/// its Client ID once. A build may also carry a default one
/// (`--dart-define=SPOTIFY_CLIENT_ID=...`).
class SpotifyConfig {
  static const defaultClientId = String.fromEnvironment('SPOTIFY_CLIENT_ID');
  static const callbackScheme = 'freesounder';
  static const customRedirect = 'freesounder://spotify-callback';
  static const loopbackPort = 43821;
  static const loopbackRedirect = 'http://127.0.0.1:43821/callback';
  static const dashboardUrl = 'https://developer.spotify.com/dashboard';

  static const scopes = [
    'user-read-private',
    'user-read-email',
    'user-read-playback-state',
    'user-modify-playback-state',
    'user-read-currently-playing',
    'user-read-recently-played',
    'user-top-read',
    'playlist-read-private',
    'playlist-read-collaborative',
    'user-library-read',
    'user-follow-read',
    // Lets the speaker (go-librespot) sign in with this token once.
    'streaming',
  ];
}

class SpotifyAuthException implements Exception {
  SpotifyAuthException(this.message, {this.needsSignIn = false, this.code});

  final String message;
  final bool needsSignIn;
  final String? code;

  @override
  String toString() => message;
}

class SpotifyTokens {
  const SpotifyTokens({required this.accessToken, required this.expiresAt, this.refreshToken, this.scope});

  final String accessToken;
  final String? refreshToken;
  final DateTime expiresAt;
  final String? scope;

  bool get expiresSoon => DateTime.now().isAfter(expiresAt.subtract(const Duration(seconds: 90)));

  Map<String, dynamic> toJson() => {
        'access_token': accessToken,
        'refresh_token': refreshToken,
        'expires_at': expiresAt.toIso8601String(),
        'scope': scope,
      };

  factory SpotifyTokens.fromJson(Map<String, dynamic> json) => SpotifyTokens(
        accessToken: json['access_token'] as String,
        refreshToken: json['refresh_token'] as String?,
        expiresAt: DateTime.tryParse(json['expires_at'] as String? ?? '') ?? DateTime.now(),
        scope: json['scope'] as String?,
      );

  /// From Spotify's /api/token answer. Keeps [previous]'s refresh token when
  /// Spotify does not send a new one.
  factory SpotifyTokens.fromTokenResponse(Map<String, dynamic> json, {SpotifyTokens? previous}) {
    final seconds = (json['expires_in'] as num?)?.toInt() ?? 3600;
    return SpotifyTokens(
      accessToken: json['access_token'] as String,
      refreshToken: json['refresh_token'] as String? ?? previous?.refreshToken,
      expiresAt: DateTime.now().add(Duration(seconds: seconds)),
      scope: json['scope'] as String? ?? previous?.scope,
    );
  }
}

/// Where secrets live: the iOS Keychain / Android Keystore in the app.
abstract class SecretStore {
  Future<String?> read(String key);
  Future<void> write(String key, String? value);
}

class SecureSecretStore implements SecretStore {
  SecureSecretStore([FlutterSecureStorage? storage])
      : _storage = storage ??
            const FlutterSecureStorage(
              iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock),
            );

  final FlutterSecureStorage _storage;

  @override
  Future<String?> read(String key) => _storage.read(key: key);

  @override
  Future<void> write(String key, String? value) =>
      value == null ? _storage.delete(key: key) : _storage.write(key: key, value: value);
}

class MemorySecretStore implements SecretStore {
  final values = <String, String>{};

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String? value) async {
    if (value == null) {
      values.remove(key);
    } else {
      values[key] = value;
    }
  }
}

typedef WebAuthenticator = Future<String> Function(String url, String callbackScheme);

Future<String> _defaultAuthenticate(String url, String scheme) => FlutterWebAuth2.authenticate(
      url: url,
      callbackUrlScheme: scheme,
      options: const FlutterWebAuth2Options(preferEphemeral: false),
    );

class SpotifyAuth {
  SpotifyAuth({
    required SecretStore store,
    http.Client? httpClient,
    WebAuthenticator? authenticate,
    this.defaultClientId = SpotifyConfig.defaultClientId,
  })  : _store = store,
        _http = httpClient ?? http.Client(),
        _authenticate = authenticate ?? _defaultAuthenticate;

  static const _kClientId = 'spotify.client_id';
  static const _kTokens = 'spotify.tokens';
  static const _kLoopback = 'spotify.loopback';
  static final _tokenUri = Uri.parse('https://accounts.spotify.com/api/token');

  final SecretStore _store;
  final http.Client _http;
  final WebAuthenticator _authenticate;
  final String defaultClientId;
  SpotifyTokens? _tokens;
  Future<SpotifyTokens>? _refreshing;

  Future<String?> clientId() async {
    final own = (await _store.read(_kClientId))?.trim();
    if (own != null && own.isNotEmpty) {
      return own;
    }
    return defaultClientId.isEmpty ? null : defaultClientId;
  }

  Future<bool> hasOwnClientId() async => ((await _store.read(_kClientId)) ?? '').trim().isNotEmpty;

  /// Save the household's own Client ID (null to go back to the built-in one).
  /// Changing it signs out, since tokens belong to one Client ID.
  Future<void> setClientId(String? value) async {
    final cleaned = value?.trim();
    if (cleaned != null && cleaned.isNotEmpty && !RegExp(r'^[0-9a-fA-F]{32}$').hasMatch(cleaned)) {
      throw SpotifyAuthException('That does not look like a Spotify Client ID (32 letters and digits).');
    }
    final before = await clientId();
    await _store.write(_kClientId, cleaned == null || cleaned.isEmpty ? null : cleaned);
    if (await clientId() != before) {
      await signOut();
    }
  }

  Future<bool> useLoopback() async => (await _store.read(_kLoopback)) == '1';

  Future<void> setUseLoopback(bool value) => _store.write(_kLoopback, value ? '1' : null);

  Future<String> redirectUri() async =>
      await useLoopback() ? SpotifyConfig.loopbackRedirect : SpotifyConfig.customRedirect;

  Future<SpotifyTokens?> tokens() async {
    if (_tokens != null) {
      return _tokens;
    }
    final raw = await _store.read(_kTokens);
    if (raw == null) {
      return null;
    }
    try {
      return _tokens = SpotifyTokens.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } on Object {
      await _store.write(_kTokens, null);
      return null;
    }
  }

  Future<bool> get signedIn async => (await tokens()) != null;

  Future<void> _save(SpotifyTokens tokens) async {
    _tokens = tokens;
    await _store.write(_kTokens, jsonEncode(tokens.toJson()));
  }

  Future<void> signOut() async {
    _tokens = null;
    await _store.write(_kTokens, null);
  }

  /// Run the browser sign-in. Throws [SpotifyAuthException] with a reason a
  /// person can act on.
  Future<void> signIn() async {
    final id = await clientId();
    if (id == null) {
      throw SpotifyAuthException('Add your Spotify Client ID first.');
    }
    final redirect = await redirectUri();
    final verifier = _randomString(64);
    final challenge = base64Url.encode(sha256.convert(utf8.encode(verifier)).bytes).replaceAll('=', '');
    final state = _randomString(16);
    final url = Uri.https('accounts.spotify.com', '/authorize', {
      'response_type': 'code',
      'client_id': id,
      'scope': SpotifyConfig.scopes.join(' '),
      'redirect_uri': redirect,
      'state': state,
      'code_challenge_method': 'S256',
      'code_challenge': challenge,
    });
    LoopbackBounce? bounce;
    if (redirect == SpotifyConfig.loopbackRedirect) {
      bounce = await LoopbackBounce.start(SpotifyConfig.loopbackPort, SpotifyConfig.customRedirect);
    }
    final String result;
    try {
      result = await _authenticate(url.toString(), SpotifyConfig.callbackScheme);
    } on Object catch (err) {
      final text = err.toString();
      if (text.contains('CANCELED') || text.contains('cancel')) {
        throw SpotifyAuthException('Sign-in cancelled.');
      }
      throw SpotifyAuthException('Could not open Spotify sign-in: $text');
    } finally {
      await bounce?.close();
    }
    final params = Uri.parse(result).queryParameters;
    if (params['error'] != null) {
      throw SpotifyAuthException(_explainAuthorizeError(params['error']!), code: params['error']);
    }
    if (params['state'] != state || params['code'] == null) {
      throw SpotifyAuthException('Spotify sign-in did not complete. Try again.');
    }
    final response = await _http.post(_tokenUri, body: {
      'grant_type': 'authorization_code',
      'code': params['code']!,
      'redirect_uri': redirect,
      'client_id': id,
      'code_verifier': verifier,
    });
    if (response.statusCode != 200) {
      throw SpotifyAuthException('Spotify refused the sign-in: ${_tokenError(response)}');
    }
    await _save(SpotifyTokens.fromTokenResponse(jsonDecode(response.body) as Map<String, dynamic>));
  }

  /// A valid access token, refreshed when it is about to expire.
  Future<String> accessToken({bool forceRefresh = false}) async {
    final current = await tokens();
    if (current == null) {
      throw SpotifyAuthException('Sign in to Spotify first.', needsSignIn: true);
    }
    if (!forceRefresh && !current.expiresSoon) {
      return current.accessToken;
    }
    final pending = _refreshing ??= _refresh(current).whenComplete(() => _refreshing = null);
    return (await pending).accessToken;
  }

  Future<SpotifyTokens> _refresh(SpotifyTokens current) async {
    final id = await clientId();
    final refresh = current.refreshToken;
    if (id == null || refresh == null) {
      await signOut();
      throw SpotifyAuthException('Sign in to Spotify again.', needsSignIn: true);
    }
    final response = await _http.post(_tokenUri, body: {
      'grant_type': 'refresh_token',
      'refresh_token': refresh,
      'client_id': id,
    });
    if (response.statusCode == 400 || response.statusCode == 401) {
      await signOut();
      throw SpotifyAuthException('Spotify signed you out (${_tokenError(response)}). Sign in again.',
          needsSignIn: true);
    }
    if (response.statusCode != 200) {
      throw SpotifyAuthException('Could not refresh the Spotify sign-in (HTTP ${response.statusCode}).');
    }
    final next = SpotifyTokens.fromTokenResponse(jsonDecode(response.body) as Map<String, dynamic>, previous: current);
    await _save(next);
    return next;
  }

  static String _tokenError(http.Response response) {
    try {
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      return [data['error'], data['error_description']].whereType<String>().join(': ');
    } on Object {
      return 'HTTP ${response.statusCode}';
    }
  }

  static String _explainAuthorizeError(String error) {
    if (error == 'access_denied') {
      return 'Spotify access was not allowed.';
    }
    return 'Spotify said: $error';
  }

  static String _randomString(int length) {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~';
    final random = Random.secure();
    return List.generate(length, (_) => chars[random.nextInt(chars.length)]).join();
  }
}
