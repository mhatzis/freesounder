import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/control_client.dart';
import '../models/now_playing.dart';
import '../state/playback_store.dart';
import 'spotify_api.dart';
import 'spotify_auth.dart';
import 'spotify_models.dart';

/// Overridable in tests (Keychain on devices).
final secretStoreProvider = Provider<SecretStore>((ref) => SecureSecretStore());

final spotifyAuthProvider = Provider<SpotifyAuth>((ref) => SpotifyAuth(store: ref.watch(secretStoreProvider)));

final spotifyApiProvider = Provider<SpotifyApi>((ref) => SpotifyApi(ref.watch(spotifyAuthProvider)));

final spotifyProvider = NotifierProvider<SpotifyController, SpotifyState>(SpotifyController.new);

class SpotifyState {
  const SpotifyState({
    this.loaded = false,
    this.clientId,
    this.ownClientId = false,
    this.loopback = false,
    this.signedIn = false,
    this.displayName,
    this.busy = false,
    this.error,
  });

  final bool loaded;
  final String? clientId;
  final bool ownClientId;
  final bool loopback;
  final bool signedIn;
  final String? displayName;
  final bool busy;
  final String? error;

  bool get configured => clientId != null;

  SpotifyState copyWith({
    bool? loaded,
    String? clientId,
    bool clearClientId = false,
    bool? ownClientId,
    bool? loopback,
    bool? signedIn,
    String? displayName,
    bool? busy,
    String? error,
    bool clearError = false,
  }) {
    return SpotifyState(
      loaded: loaded ?? this.loaded,
      clientId: clearClientId ? null : (clientId ?? this.clientId),
      ownClientId: ownClientId ?? this.ownClientId,
      loopback: loopback ?? this.loopback,
      signedIn: signedIn ?? this.signedIn,
      displayName: displayName ?? this.displayName,
      busy: busy ?? this.busy,
      error: clearError ? null : (error ?? this.error),
    );
  }
}

/// Spotify sign-in, browsing and "play this on that room".
///
/// Playing goes to the group leader's go-librespot over the player API (no
/// Spotify app needed, no device list). The first time, the app hands the
/// speaker its Spotify token so it signs in; the speaker then keeps its own
/// credentials. Web API playback on the speaker's Connect device is the
/// fallback for players that don't have the endpoint.
class SpotifyController extends Notifier<SpotifyState> {
  @override
  SpotifyState build() {
    unawaited(Future<void>.microtask(load));
    return const SpotifyState();
  }

  SpotifyAuth get _auth => ref.read(spotifyAuthProvider);
  SpotifyApi get _api => ref.read(spotifyApiProvider);

  Future<void> load() async {
    try {
      final id = await _auth.clientId();
      state = state.copyWith(
        loaded: true,
        clientId: id,
        clearClientId: id == null,
        ownClientId: await _auth.hasOwnClientId(),
        loopback: await _auth.useLoopback(),
        signedIn: await _auth.signedIn,
      );
      if (state.signedIn) {
        unawaited(_loadName());
      }
    } on Object catch (err) {
      state = state.copyWith(loaded: true, error: 'Could not read the saved Spotify sign-in: $err');
    }
  }

  Future<void> _loadName() async {
    try {
      final me = await _api.me(refresh: true);
      state = state.copyWith(displayName: (me['display_name'] ?? me['id']) as String?);
    } on SpotifyAuthException catch (err) {
      state = state.copyWith(signedIn: !err.needsSignIn, error: err.message);
    } on Object {
      // Offline: keep the saved sign-in.
    }
  }

  Future<void> saveClientId(String value) async {
    state = state.copyWith(clearError: true);
    try {
      await _auth.setClientId(value);
    } on SpotifyAuthException catch (err) {
      state = state.copyWith(error: err.message);
      return;
    }
    _api.forgetUser();
    await load();
  }

  Future<void> setLoopback(bool value) async {
    await _auth.setUseLoopback(value);
    state = state.copyWith(loopback: value);
  }

  Future<bool> signIn() async {
    state = state.copyWith(busy: true, clearError: true);
    try {
      await _auth.signIn();
      _api.forgetUser();
      state = state.copyWith(signedIn: true, busy: false);
      await _loadName();
      return true;
    } on SpotifyAuthException catch (err) {
      state = state.copyWith(busy: false, error: err.message);
    } on Object catch (err) {
      state = state.copyWith(busy: false, error: 'Spotify sign-in failed: $err');
    }
    return false;
  }

  Future<void> signOut() async {
    await _auth.signOut();
    _api.forgetUser();
    state = state.copyWith(signedIn: false, displayName: '', clearError: true);
  }

  /// Home shelves. Each one fails on its own (e.g. an endpoint Spotify
  /// switched off) without hiding the others.
  Future<List<SpotifySection>> home() async {
    Future<SpotifySection> shelf(String title, Future<List<SpotifyItem>> Function() load) async {
      try {
        return SpotifySection(title, await load());
      } on SpotifyAuthException {
        rethrow;
      } on Object {
        return SpotifySection(title, const []);
      }
    }

    final shelves = await Future.wait([
      shelf('Recently played', _api.recentlyPlayed),
      shelf('Your playlists', _api.myPlaylists),
      shelf('Saved albums', _api.savedAlbums),
      shelf('Artists you follow', _api.followedArtists),
      shelf('Podcasts', _api.savedShows),
    ]);
    return shelves.where((s) => s.items.isNotEmpty).toList();
  }

  Future<List<SpotifySection>> search(String query) => _api.search(query);

  Future<SpotifyPage> page(SpotifyItem item) async {
    switch (item.type) {
      case 'album':
        return _api.album(item.id);
      case 'artist':
        return _api.artist(item.id);
      case 'show':
        return _api.show(item.id);
      case 'playlist':
        final page = await _api.playlist(item.id);
        if (page.tracks.isNotEmpty) {
          return page;
        }
        return _playlistFromSpeaker(page);
      default:
        throw SpotifyApiException(400, 'Nothing to open');
    }
  }

  /// Ask the speaker's go-librespot for a playlist's songs (it can list any
  /// playlist once signed in).
  Future<SpotifyPage> _playlistFromSpeaker(SpotifyPage page) async {
    final playback = ref.read(playbackProvider);
    final client = ref.read(playbackProvider.notifier).client;
    final target = playback.activeRoom?.id;
    if (client == null || target == null) {
      return page;
    }
    try {
      final rows = await client.spotifyContextTracks(target, page.item.uri);
      final tracks = <SpotifyItem>[];
      for (final row in rows) {
        final t = row['track'];
        final uri = row['uri'] as String?;
        if (uri == null) {
          continue;
        }
        tracks.add(SpotifyItem(
          type: 'track',
          id: uri.split(':').last,
          uri: uri,
          name: (t is Map ? t['name'] as String? : null) ?? 'Song',
          subtitle: t is Map ? (t['artist_names'] as List?)?.join(', ') : null,
          imageUrl: t is Map ? t['album_cover_url'] as String? : null,
          durationMs: t is Map ? (t['duration'] as num?)?.toInt() : null,
          contextUri: page.item.uri,
        ));
      }
      if (tracks.isEmpty) {
        return page;
      }
      return SpotifyPage(item: page.item, imageUrl: page.imageUrl, tracks: tracks);
    } on Object {
      return page;
    }
  }

  /// Play [item] on [targetId] (a room id or group id). Returns the group
  /// name it plays on. Throws with a message a person can act on.
  Future<String?> play(String targetId, SpotifyItem item) async {
    final playback = ref.read(playbackProvider.notifier);
    final String uri;
    String? skipTo;
    if ((item.type == 'track' || item.type == 'episode') && item.contextUri != null) {
      uri = item.contextUri!;
      skipTo = item.uri;
    } else {
      uri = item.uri;
    }
    if (ref.read(playbackProvider).mockMode) {
      playback.showDemoNowPlaying(
        targetId,
        NowPlaying(
          playerId: targetId,
          title: item.name,
          artist: item.subtitle,
          imageUrl: item.imageUrl,
          state: 'playing',
          source: 'spotify',
          position: 0,
          duration: item.durationMs == null ? null : item.durationMs! / 1000,
          receivedAt: DateTime.now(),
        ),
      );
      return null;
    }
    final client = playback.client;
    if (client == null) {
      throw ControlApiException('Not connected to a player');
    }
    PlayerSpotify info;
    try {
      info = await _ensureSpeaker(client, targetId);
    } on ControlApiException catch (err) {
      if (err.statusCode == 404 || err.statusCode == 405) {
        // Older player: no Spotify endpoints, use the Web API.
        await _playViaWebApi(null, uri, skipTo);
        return null;
      }
      rethrow;
    }
    try {
      await client.spotifyPlay(targetId, uri, skipToUri: skipTo);
    } on ControlApiException catch (err) {
      if (err.statusCode != 409) {
        rethrow;
      }
      info = await _ensureSpeaker(client, targetId, force: true);
      await client.spotifyPlay(targetId, uri, skipToUri: skipTo);
    }
    return info.deviceName;
  }

  Future<PlayerSpotify> _ensureSpeaker(ControlApiClient client, String targetId, {bool force = false}) async {
    final info = await client.spotifyInfo(targetId);
    final user = await _api.username();
    if (!force && info.loggedIn && info.username == user) {
      return info;
    }
    final token = await _auth.accessToken(forceRefresh: true);
    try {
      return await client.spotifyLogin(targetId, username: user, accessToken: token);
    } on ControlApiException catch (err) {
      final detail = err.detail ?? err.message;
      if (detail.toLowerCase().contains('premium')) {
        throw ControlApiException(
          'Spotify did not accept the speaker sign-in. Playing on speakers needs Spotify Premium.',
          statusCode: err.statusCode,
          detail: detail,
        );
      }
      rethrow;
    }
  }

  /// Fallback: find the speaker in Spotify's device list and play there.
  Future<void> _playViaWebApi(PlayerSpotify? info, String uri, String? skipTo) async {
    final id = await _findDevice(info);
    final isContext = !uri.startsWith('spotify:track:') && !uri.startsWith('spotify:episode:');
    await _api.play(
      id,
      contextUri: isContext ? uri : null,
      uris: isContext ? null : [uri],
      offsetUri: skipTo,
    );
  }

  Future<String> _findDevice(PlayerSpotify? info) async {
    final devices = await _api.devices();
    final playback = ref.read(playbackProvider);
    final names = <String>{
      if (info?.deviceName != null) info!.deviceName!,
      if (playback.activeRoom?.groupName != null) playback.activeRoom!.groupName!,
      if (playback.activeRoom != null) playback.activeRoom!.name,
    };
    for (final d in devices) {
      if (info?.deviceId != null && d['id'] == info!.deviceId) {
        return d['id'] as String;
      }
    }
    for (final d in devices) {
      if (names.contains(d['name'])) {
        return d['id'] as String;
      }
    }
    throw SpotifyApiException(
      404,
      'Spotify cannot see this speaker yet. Play something on it once from FreeSounder or the Spotify app.',
    );
  }

  /// Transport through the Web API when the player could not do it.
  Future<void> webControl(String targetId, String action, {int? positionMs}) async {
    final client = ref.read(playbackProvider.notifier).client;
    PlayerSpotify? info;
    if (client != null) {
      try {
        info = await client.spotifyInfo(targetId);
      } on Object {
        info = null;
      }
    }
    final device = await _findDevice(info);
    switch (action) {
      case 'pause':
        await _api.pause(device);
      case 'play':
        await _api.resume(device);
      case 'next':
        await _api.next(device);
      case 'previous':
        await _api.previous(device);
      case 'seek':
        await _api.seek(device, positionMs ?? 0);
    }
  }
}
