import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/connection_prefs.dart';
import '../api/control_client.dart';
import '../api/discovery.dart';
import '../api/first_player.dart';
import '../api/mock_data.dart';
import '../models/library.dart';
import '../models/now_playing.dart';
import '../models/podcast.dart';
import '../spotify/spotify_store.dart';
import '../models/player_settings.dart';
import '../models/radio_station.dart';
import '../models/room.dart';
import '../ui/artwork.dart' show artworkGeneration;

class AppConfig {
  const AppConfig({
    this.forceDemo = false,
    this.apiBase,
    this.discoveryTimeout = const Duration(seconds: 3),
  });

  /// When true, skip discovery and load the in-app demo household immediately.
  final bool forceDemo;

  /// Optional explicit control-api URL. When null, players are discovered
  /// via mDNS (`_freesounder._tcp`). Demo mode is off unless the user enables it.
  final String? apiBase;
  final Duration discoveryTimeout;
}

class PlaybackState {
  const PlaybackState({
    required this.rooms,
    this.activeRoomId,
    this.nowPlaying,
    this.nowPlayingByRoom = const {},
    this.mockMode = true,
    this.connected = false,
    this.discovering = false,
    this.error,
    this.hostUrl,
    this.discoveredHosts = const [],
    this.settings = const {},
    this.maState,
    this.maDetail,
    this.reconnecting = false,
  });

  final List<Room> rooms;
  final String? activeRoomId;
  final NowPlaying? nowPlaying;

  /// Latest now-playing per room (every room's group, from the event stream).
  final Map<String, NowPlaying> nowPlayingByRoom;
  final bool mockMode;
  final bool connected;
  final bool discovering;
  final String? error;
  final String? hostUrl;
  final List<DiscoveredHost> discoveredHosts;
  final Map<String, PlayerSettings> settings;

  /// Pre-v2 systems only (Music Assistant state): connected, starting,
  /// auth_required or unavailable (null when unknown or in demo).
  final String? maState;
  final String? maDetail;

  /// The live link to the player dropped (app woke from the background, Wi-Fi
  /// blipped); the store is re-fetching and reconnecting on its own.
  final bool reconnecting;

  /// Connected to a real system whose music side is not ready yet.
  bool get systemStarting => connected && !mockMode && maState != null && maState != 'connected';

  Room? get activeRoom {
    if (rooms.isEmpty) {
      return null;
    }
    for (final room in rooms) {
      if (room.id == activeRoomId) {
        return room;
      }
    }
    return rooms.first;
  }

  PlaybackState copyWith({
    List<Room>? rooms,
    String? activeRoomId,
    NowPlaying? nowPlaying,
    bool clearNowPlaying = false,
    Map<String, NowPlaying>? nowPlayingByRoom,
    bool? mockMode,
    bool? connected,
    bool? discovering,
    String? error,
    bool clearError = false,
    String? hostUrl,
    bool clearHostUrl = false,
    List<DiscoveredHost>? discoveredHosts,
    Map<String, PlayerSettings>? settings,
    String? maState,
    String? maDetail,
    bool clearMa = false,
    bool? reconnecting,
  }) {
    return PlaybackState(
      rooms: rooms ?? this.rooms,
      activeRoomId: activeRoomId ?? this.activeRoomId,
      nowPlaying: clearNowPlaying ? null : (nowPlaying ?? this.nowPlaying),
      nowPlayingByRoom: nowPlayingByRoom ?? this.nowPlayingByRoom,
      mockMode: mockMode ?? this.mockMode,
      connected: connected ?? this.connected,
      discovering: discovering ?? this.discovering,
      error: clearError ? null : (error ?? this.error),
      hostUrl: clearHostUrl ? null : (hostUrl ?? this.hostUrl),
      discoveredHosts: discoveredHosts ?? this.discoveredHosts,
      settings: settings ?? this.settings,
      maState: clearMa ? null : (maState ?? this.maState),
      maDetail: clearMa ? null : (maDetail ?? this.maDetail),
      reconnecting: reconnecting ?? this.reconnecting,
    );
  }
}

final appConfigProvider = Provider<AppConfig>((ref) {
  const api = String.fromEnvironment('API_BASE');
  const forceDemo = bool.fromEnvironment('FORCE_DEMO');
  return AppConfig(
    forceDemo: forceDemo,
    apiBase: api.isEmpty ? null : api,
  );
});

final playbackProvider = NotifierProvider<PlaybackStore, PlaybackState>(PlaybackStore.new);

/// Overridable in tests.
final controlClientFactoryProvider = Provider<ControlApiClient Function(String url)>(
  (ref) => (url) => ControlApiClient(baseUrl: url),
);

/// Liveness timings (overridable in tests).
class LinkTimings {
  const LinkTimings({
    this.heartbeat = const Duration(seconds: 12),
    this.firstRetry = const Duration(seconds: 1),
    this.maxRetry = const Duration(seconds: 15),
    this.nowPlayingRefresh = const Duration(seconds: 12),
  });

  /// Fallback re-read of the active room's now playing (foreground only).
  final Duration nowPlayingRefresh;

  /// How often a connected app checks /health while in the foreground.
  final Duration heartbeat;
  final Duration firstRetry;
  final Duration maxRetry;
}

final linkTimingsProvider = Provider<LinkTimings>((ref) => const LinkTimings());

/// The demo household (overridable in tests, e.g. a 10-room home).
final mockHouseholdProvider = Provider<MockHousehold Function()>((ref) => MockHousehold.new);

/// mDNS discovery of players (overridable in tests).
final discoveryProvider = Provider<Future<List<DiscoveredHost>> Function(Duration timeout)>(
  (ref) => (timeout) => discoverControlApis(timeout: timeout),
);

/// Overridable in tests.
final firstPlayerSetupProvider = Provider<FirstPlayerSetup Function()>((ref) => FirstPlayerSetup.new);

/// Single rooms-and-playback store. Every screen reads this; it is the source
/// of truth for the active room, groups, volumes, now-playing and sync health.
class PlaybackStore extends Notifier<PlaybackState> {
  // ---- optimistic controls -------------------------------------------------
  // What the user just did wins over older data from the players for a short
  // while, so a poll or event that left before the change can't snap a
  // slider back or flip play/pause back.
  static const holdAfterChange = Duration(milliseconds: 1500);
  static const volumeSendInterval = Duration(milliseconds: 100);
  final _volumeHolds = <String, ({int value, DateTime until})>{};
  final _playHolds = <String, ({String state, DateTime until, DateTime since})>{};

  /// How long a tapped play/pause wins over the players' reports at most;
  /// the hold ends earlier once a report read after the command agrees.
  /// It never ends while the command is still on its way: 0.6.14 let a
  /// "playing" report win when a pause took 9 s to land, so the icon ended up
  /// wrong and resuming took two presses.
  static Duration playHoldMax = const Duration(seconds: 8);

  /// After the player answered a play/pause, its reports still lag a little.
  static Duration playHoldAfterReply = const Duration(seconds: 3);
  final _playInFlight = <String, int>{};
  final _volumeLastSent = <String, DateTime>{};
  final _volumeTrailing = <String, Timer>{};

  @override
  set state(PlaybackState value) => super.state = _applyHolds(value);

  PlaybackState _applyHolds(PlaybackState s) {
    if (_volumeHolds.isEmpty && _playHolds.isEmpty) {
      return s;
    }
    final now = DateTime.now();
    _volumeHolds.removeWhere((_, h) => h.until.isBefore(now));
    _playHolds.removeWhere((id, h) => h.until.isBefore(now) && (_playInFlight[id] ?? 0) == 0);
    var rooms = s.rooms;
    if (_volumeHolds.isNotEmpty && rooms.any((r) => (_volumeHolds[r.id]?.value ?? r.volume) != r.volume)) {
      rooms = [
        for (final r in rooms)
          if (_volumeHolds[r.id] case final h? when h.value != r.volume) r.copyWith(volume: h.value) else r,
      ];
    }
    NowPlaying? held(NowPlaying? np) {
      final h = np == null ? null : _playHolds[np.playerId];
      return (np != null && h != null && np.state != h.state && np.source != null && np.source != 'none')
          ? np.copyWith(state: h.state)
          : np;
    }

    final np = held(s.nowPlaying);
    if (identical(rooms, s.rooms) && identical(np, s.nowPlaying)) {
      return s;
    }
    return s.copyWith(rooms: rooms, nowPlaying: np);
  }

  /// Slider moving: show [value] at once and send it at most every 100 ms.
  void dragVolume(String roomId, double value, {bool group = false}) {
    final level = value.round();
    _holdVolume(roomId, level);
    final last = _volumeLastSent[roomId];
    final now = DateTime.now();
    if (last == null || now.difference(last) >= volumeSendInterval) {
      _volumeTrailing.remove(roomId)?.cancel();
      _volumeLastSent[roomId] = now;
      unawaited(_sendVolume(roomId, level, group: group).catchError((_) {}));
    } else {
      _volumeTrailing.remove(roomId)?.cancel();
      _volumeTrailing[roomId] = Timer(volumeSendInterval - now.difference(last), () {
        _volumeTrailing.remove(roomId);
        _volumeLastSent[roomId] = DateTime.now();
        final latest = _volumeHolds[roomId]?.value ?? level;
        unawaited(_sendVolume(roomId, latest, group: group).catchError((_) {}));
      });
    }
  }

  /// Slider released: send the final value now (and keep showing it).
  Future<void> commitVolume(String roomId, double value, {bool group = false}) async {
    final level = value.round();
    _volumeTrailing.remove(roomId)?.cancel();
    _holdVolume(roomId, level);
    _volumeLastSent[roomId] = DateTime.now();
    await _sendVolume(roomId, level, group: group);
    _holdVolume(roomId, level); // hold a little longer after the player confirms
  }

  void _holdVolume(String roomId, int level) {
    _volumeHolds[roomId] = (value: level, until: DateTime.now().add(holdAfterChange));
    state = state; // re-apply holds to the rooms on screen
  }

  Future<void> _sendVolume(String roomId, int level, {required bool group}) =>
      group ? setGroupVolume(roomId, level) : setVolume(roomId, level);

  ControlApiClient? _client;
  StreamSubscription<Map<String, dynamic>>? _events;
  Timer? _healthTimer;
  Timer? _heartbeat;
  Timer? _retryTimer;
  Duration? _retryDelay;
  bool _resyncing = false;
  int _failedResyncs = 0;
  bool _foreground = true;
  bool _disposed = false;
  String? _householdId;
  bool _persistHome = false;
  late MockHousehold _mock;

  /// Household id of the connected system (from /api/v2/info), when known.
  String? get householdId => _householdId;

  ControlApiClient? get client => _client;
  MockHousehold get mockHousehold => _mock;

  @override
  PlaybackState build() {
    _mock = ref.read(mockHouseholdProvider)();
    final config = ref.watch(appConfigProvider);
    ref.onDispose(() {
      _disposed = true;
      _healthTimer?.cancel();
      _heartbeat?.cancel();
      _retryTimer?.cancel();
      _events?.cancel();
      _npCheck?.cancel();
      _npRefresh?.cancel();
      for (final t in _volumeTrailing.values) {
        t.cancel();
      }
      _client?.close();
    });
    if (config.forceDemo) {
      return _demoState();
    }
    Future.microtask(_connectOrDemo);
    return const PlaybackState(rooms: [], mockMode: false, connected: false, discovering: true);
  }

  PlaybackState _demoState({String? error, List<DiscoveredHost>? hosts}) {
    return PlaybackState(
      rooms: List.of(_mock.rooms),
      activeRoomId: _mock.activeRoomId,
      nowPlaying: _mock.nowPlaying[_mock.activeRoomId],
      mockMode: true,
      discovering: false,
      connected: true,
      error: error,
      hostUrl: null,
      discoveredHosts: hosts ?? const [],
      settings: Map.of(_mock.settings),
    );
  }

  PlaybackState _emptyState({String? error, List<DiscoveredHost>? hosts, String? hostUrl}) {
    return PlaybackState(
      rooms: const [],
      mockMode: false,
      discovering: false,
      connected: false,
      error: error,
      hostUrl: hostUrl,
      discoveredHosts: hosts ?? const [],
    );
  }

  Future<void> _connectOrDemo() async {
    final config = ref.read(appConfigProvider);
    final prefs = ref.read(connectionPrefsProvider);
    if (prefs?.preferDemo == true && config.apiBase == null) {
      final hosts = await _safeDiscover(config.discoveryTimeout);
      state = _demoState(hosts: hosts);
      return;
    }
    var base = config.apiBase ?? prefs?.savedHost;
    List<DiscoveredHost> hosts = const [];
    if (base == null) {
      hosts = await _safeDiscover(config.discoveryTimeout);
      final ready = hosts.where((h) => !h.unclaimed);
      if (ready.isNotEmpty) {
        base = ready.first.url;
      }
    } else {
      unawaited(_refreshHosts());
    }
    if (base == null) {
      state = _emptyState(hosts: hosts);
      return;
    }
    final ok = await connectTo(base, persist: false);
    if (!ok) {
      // The saved host moved or is gone: look for the same home first (its
      // other members, mDNS by household id).
      final seen = await _reconnectToHome();
      if (_client != null) {
        return;
      }
      hosts = state.discoveredHosts;
      if (!seen) {
        // Nothing of the saved home exists here any more (reset/reflashed):
        // any set-up player on this network is the home now.
        for (final host in hosts.where((h) => !h.unclaimed && h.url != base)) {
          if (await connectTo(host.url, persist: true)) {
            return;
          }
        }
      }
      state = _emptyState(
        error: 'Could not reach your system.',
        hosts: hosts,
        hostUrl: base,
      );
    }
  }

  Future<List<DiscoveredHost>> _safeDiscover(Duration timeout) async {
    if (timeout <= Duration.zero) {
      return const [];
    }
    try {
      return await ref.read(discoveryProvider)(timeout);
    } on Object {
      return const [];
    }
  }

  Future<void> _refreshHosts() async {
    final config = ref.read(appConfigProvider);
    final hosts = await _safeDiscover(config.discoveryTimeout);
    state = state.copyWith(discoveredHosts: hosts);
  }

  Future<void> refreshDiscovery() => _refreshHosts();

  /// Connect to a household control API. Returns whether the connection worked.
  Future<bool> connectTo(
    String rawUrl, {
    bool persist = true,
  }) async {
    final url = _normaliseUrl(rawUrl);
    if (url == null) {
      state = state.copyWith(error: 'Enter a host like 192.168.1.10 or http://host:8080');
      return false;
    }
    final previous = state;
    await _teardownClient();
    state = state.copyWith(discovering: true, clearError: true, hostUrl: url);
    final client = ref.read(controlClientFactoryProvider)(url);
    try {
      final rooms = await client.listRooms().timeout(const Duration(seconds: 4));
      final activeId = rooms.isNotEmpty ? rooms.first.id : null;
      NowPlaying? np;
      if (activeId != null) {
        np = await client.nowPlaying(activeId).timeout(const Duration(seconds: 3));
      }
      _client = client;
      state = state.copyWith(
        rooms: rooms,
        activeRoomId: activeId,
        nowPlaying: np,
        mockMode: false,
        connected: true,
        discovering: false,
        hostUrl: url,
        clearError: true,
      );
      final prefs = ref.read(connectionPrefsProvider);
      if (persist && prefs != null) {
        await prefs.setSavedHost(url);
        await prefs.setPreferDemo(false);
      }
      _subscribeEvents(client);
      _startHeartbeat();
      _persistHome = persist || url == prefs?.savedHost || prefs?.savedHost == null;
      unawaited(_loadHouseholdId(client));
      await refreshHealth();
      _healthTimer?.cancel();
      _healthTimer = Timer.periodic(const Duration(seconds: 5), (_) {
        if (state.systemStarting || state.rooms.isEmpty) {
          unawaited(refreshHealth(reloadRooms: true));
        }
      });
      return true;
    } on Object catch (err) {
      await client.close();
      final why = err is ControlApiException
          ? err.message
          : (err is TimeoutException ? 'no answer in time' : err.runtimeType.toString());
      if (previous.mockMode) {
        state = _demoState(
          error: 'Could not connect to $url ($why)',
          hosts: previous.discoveredHosts,
        );
      } else {
        state = previous.copyWith(
          rooms: previous.rooms,
          discovering: false,
          connected: false,
          mockMode: false,
          error: 'Could not connect to $url ($why)',
          hostUrl: url,
        );
      }
      return false;
    }
  }

  Future<void> useDemo({bool persist = true}) async {
    await _teardownClient();
    final prefs = ref.read(connectionPrefsProvider);
    if (persist && prefs != null) {
      await prefs.setPreferDemo(true);
    }
    state = _demoState(hosts: state.discoveredHosts);
  }

  Future<void> reconnect() async {
    final url = state.hostUrl ?? ref.read(connectionPrefsProvider)?.savedHost;
    if (url != null) {
      await connectTo(url, persist: true);
      return;
    }
    await _connectOrDemo();
  }

  Future<void> leaveDemoAndDiscover() async {
    final prefs = ref.read(connectionPrefsProvider);
    if (prefs != null) {
      await prefs.setPreferDemo(false);
    }
    state = state.copyWith(discovering: true, mockMode: false, connected: false, clearError: true);
    await _connectOrDemo();
  }

  /// Ask the connected player whether it is healthy (never throws).
  Future<void> refreshHealth({bool reloadRooms = false}) async {
    final client = _client;
    if (client == null) {
      return;
    }
    try {
      final health = await client.health().timeout(const Duration(seconds: 4));
      state = state.copyWith(maState: health.maState, maDetail: health.maDetail ?? '');
      if (reloadRooms && health.maConnected) {
        final rooms = await client.listRooms().timeout(const Duration(seconds: 4));
        state = state.copyWith(
          rooms: rooms,
          activeRoomId: state.activeRoomId ?? (rooms.isNotEmpty ? rooms.first.id : null),
        );
      }
    } on Object {
      // Keep the last known state; the next tick tries again.
    }
  }

  Future<void> _loadHouseholdId(ControlApiClient client) async {
    try {
      final info = await client.info().timeout(const Duration(seconds: 4));
      final hh = info['household'];
      if (identical(client, _client) && hh is Map && hh['id'] is String) {
        _householdId = hh['id'] as String;
        if (_persistHome) {
          await _rememberHome(client);
        }
      }
    } on Object {
      // Optional: only used to spot players that belong to another home.
    }
  }

  /// Players on this network that are set up in a *different* home (for
  /// example a second board that was set up while the phone could not reach
  /// the first one). The add-player sheet offers to move them here.
  Future<List<UnclaimedPlayer>> listOtherHomePlayers() async {
    if (state.mockMode || _client == null) {
      return const [];
    }
    final ours = _householdId;
    final roomIds = {for (final r in state.rooms) r.id};
    final connectedHost = Uri.tryParse(_client!.baseUrl)?.host;
    final hosts = await _safeDiscover(ref.read(appConfigProvider).discoveryTimeout);
    return [
      for (final h in hosts)
        if (h.state == 'ready' &&
            h.household != null &&
            ours != null &&
            h.household != ours &&
            !roomIds.contains(h.id) &&
            Uri.tryParse(h.url)?.host != connectedHost)
          UnclaimedPlayer(
            id: h.id ?? h.url,
            name: h.name,
            url: h.url,
            address: Uri.tryParse(h.url)?.host,
            state: 'other-home',
          ),
    ];
  }

  // --- Staying live: event-stream reconnect, heartbeat, app resume ---------

  void _subscribeEvents(ControlApiClient client) {
    unawaited(_events?.cancel());
    _events = client.connectEvents().listen(
      _onEvent,
      onError: (Object _) => _linkLost(client),
      onDone: () => _linkLost(client),
      cancelOnError: true,
    );
  }

  void _startHeartbeat() {
    _heartbeat?.cancel();
    _npRefresh?.cancel();
    if (!_foreground) {
      return;
    }
    _heartbeat = Timer.periodic(ref.read(linkTimingsProvider).heartbeat, (_) => unawaited(_beat()));
    // Safety net under the event stream: re-read the active room's now
    // playing every so often while the app is on screen, so a missed event
    // can never leave an old song and cover showing.
    _npRefresh?.cancel();
    _npRefresh = Timer.periodic(ref.read(linkTimingsProvider).nowPlayingRefresh, (_) {
      final active = state.activeRoomId;
      if (active != null && !state.reconnecting && !state.mockMode) {
        unawaited(_refreshNowPlaying(active));
      }
    });
  }

  Timer? _npRefresh;

  Future<void> _beat() async {
    final client = _client;
    if (client == null || state.reconnecting) {
      return;
    }
    try {
      await client.health().timeout(const Duration(seconds: 5));
    } on Object {
      _linkLost(client);
    }
  }

  /// The event stream ended or a heartbeat failed: show "Reconnecting…" and
  /// retry with backoff (1 s, 2 s, 4 s … 15 s) until the player answers.
  void _linkLost(ControlApiClient client) {
    if (_disposed || !identical(client, _client) || state.mockMode) {
      return;
    }
    state = state.copyWith(reconnecting: true);
    if (_retryTimer?.isActive ?? false) {
      return;
    }
    if (!_foreground) {
      return; // resume() reconnects; no point retrying while suspended
    }
    final timings = ref.read(linkTimingsProvider);
    final delay = _retryDelay ?? timings.firstRetry;
    final doubled = delay * 2;
    _retryDelay = doubled > timings.maxRetry ? timings.maxRetry : doubled;
    _retryTimer = Timer(delay, () => unawaited(_resync(client)));
  }

  /// Re-fetch rooms, now playing and settings and open a fresh event stream.
  Future<bool> _resync(ControlApiClient client, {bool eventsOpen = false}) async {
    if (_disposed || !identical(client, _client) || _resyncing) {
      return false;
    }
    _resyncing = true;
    try {
      final rooms = await client.listRooms().timeout(const Duration(seconds: 4));
      final activeId = rooms.any((r) => r.id == state.activeRoomId)
          ? state.activeRoomId
          : (rooms.isNotEmpty ? rooms.first.id : null);
      NowPlaying? np;
      final askedAt = DateTime.now();
      if (activeId != null) {
        np = await client.nowPlaying(activeId).timeout(const Duration(seconds: 4));
      }
      if (!identical(client, _client)) {
        return false;
      }
      if (np != null) {
        np = _notOlderThanCommand(np, askedAt);
        _confirmPlayHold(np, askedAt);
      }
      if (!eventsOpen) {
        _subscribeEvents(client);
      }
      _failedResyncs = 0;
      _retryDelay = null;
      _retryTimer?.cancel();
      state = PlaybackState(
        rooms: rooms,
        activeRoomId: activeId,
        nowPlaying: np,
        nowPlayingByRoom: np == null
            ? state.nowPlayingByRoom
            : {...state.nowPlayingByRoom, np.playerId: np},
        mockMode: false,
        connected: true,
        discovering: false,
        hostUrl: state.hostUrl,
        discoveredHosts: state.discoveredHosts,
        settings: state.settings,
        maState: state.maState,
        maDetail: state.maDetail,
        reconnecting: false,
      );
      _startHeartbeat();
      unawaited(_refreshOpenSettings(client));
      return true;
    } on Object {
      _resyncing = false;
      _failedResyncs++;
      if (_failedResyncs >= 3 && await _failOverToAnotherPlayer(client)) {
        return true;
      }
      _linkLost(client);
      return false;
    } finally {
      _resyncing = false;
    }
  }

  /// Every player can serve the whole home: if the one we talk to stays
  /// unreachable, switch to another room's player.
  Future<bool> _failOverToAnotherPlayer(ControlApiClient dead) async {
    final deadHost = Uri.tryParse(dead.baseUrl)?.host;
    final others = <String>{
      for (final room in state.rooms)
        if (room.url != null && Uri.tryParse(room.url!)?.host != deadHost) room.url!,
    };
    for (final url in others) {
      if (!identical(dead, _client) || _disposed) {
        return false;
      }
      if (await _canReach(url) && await connectTo(url, persist: true)) {
        _failedResyncs = 0;
        return true;
      }
    }
    return false;
  }

  Future<bool> _canReach(String url) async {
    final probe = ref.read(controlClientFactoryProvider)(url);
    try {
      await probe.health().timeout(const Duration(seconds: 3));
      return true;
    } on Object {
      return false;
    } finally {
      await probe.close();
    }
  }

  Future<void> _refreshOpenSettings(ControlApiClient client) async {
    for (final roomId in state.settings.keys.toList()) {
      try {
        final fresh = await client.getSettings(roomId).timeout(const Duration(seconds: 4));
        state = state.copyWith(settings: {...state.settings, roomId: fresh});
      } on Object {
        // The settings sheet fetches again when opened.
      }
    }
  }

  /// App went to the background: stop the heartbeat (iOS suspends sockets).
  void appPaused() {
    _foreground = false;
    _heartbeat?.cancel();
    _npRefresh?.cancel();
    _retryTimer?.cancel();
  }

  /// App came back: whatever the socket looks like, re-fetch everything and
  /// reopen the event stream so the screen is never stale after a wake.
  Future<void> appResumed() async {
    _foreground = true;
    artworkGeneration.value++; // reload art that failed while suspended
    final client = _client;
    if (client == null || state.mockMode) {
      return;
    }
    _retryDelay = null;
    _retryTimer?.cancel();
    // What is on screen is from before the phone slept: mark it stale, reopen
    // the event stream at once (its snapshot usually lands first) and fetch
    // in parallel. A play/pause tapped meanwhile is sent as an explicit
    // command and held until a report read after it agrees.
    state = state.copyWith(reconnecting: true);
    _subscribeEvents(client);
    await _resync(client, eventsOpen: true);
  }

  Future<void> _teardownClient() async {
    _householdId = null;
    _healthTimer?.cancel();
    _healthTimer = null;
    _heartbeat?.cancel();
    _heartbeat = null;
    _npRefresh?.cancel();
    _npRefresh = null;
    _retryTimer?.cancel();
    _retryTimer = null;
    _retryDelay = null;
    await _events?.cancel();
    _events = null;
    await _client?.close();
    _client = null;
  }

  void _onEvent(Map<String, dynamic> message) {
    final event = message['event'] as String? ?? '';
    final data = message['data'];
    if (event == 'rooms' && data is List) {
      final rooms = [
        for (final item in data) Room.fromJson(item as Map<String, dynamic>),
      ];
      // The rooms list carries each room's song and art too. If a room's
      // now playing disagrees (a missed event), drop it so the row shows the
      // fresh values, and re-fetch the active room's.
      final byRoom = Map<String, NowPlaying>.of(state.nowPlayingByRoom);
      var activeStale = false;
      for (final room in rooms) {
        final np = byRoom[room.id];
        if (np != null && (np.title != room.nowTitle || np.imageUrl != room.nowImageUrl)) {
          byRoom.remove(room.id);
          activeStale = activeStale || room.id == state.activeRoomId;
        }
      }
      state = state.copyWith(rooms: rooms, nowPlayingByRoom: byRoom, connected: true, clearError: true);
      if (activeStale) {
        _scheduleNowPlayingCheck();
      }
    } else if (event == 'now_playing' && data is Map<String, dynamic>) {
      final np = NowPlaying.fromJson(data);
      _confirmPlayHold(np, DateTime.now());
      final byRoom = Map<String, NowPlaying>.of(state.nowPlayingByRoom)..[np.playerId] = np;
      state = state.copyWith(
        nowPlayingByRoom: byRoom,
        nowPlaying: np.playerId == state.activeRoomId ? np : null,
      );
    } else if (event == 'health' && data is Map<String, dynamic>) {
      final ok = data['status'] == 'ok';
      state = state.copyWith(connected: ok);
    }
  }

  void selectRoom(String roomId) {
    if (state.mockMode) {
      _mock.activeRoomId = roomId;
      state = state.copyWith(
        activeRoomId: roomId,
        nowPlaying: _mock.nowPlaying[roomId],
      );
      return;
    }
    // Show what we already know for this room right away (never the previous
    // room's song and cover), then confirm with the player.
    final known = state.nowPlayingByRoom[roomId];
    state = state.copyWith(activeRoomId: roomId, nowPlaying: known, clearNowPlaying: known == null);
    unawaited(_refreshNowPlaying(roomId));
  }

  Timer? _npCheck;

  /// The rooms list says the active room's song changed but no now-playing
  /// event followed: fetch it (once, shortly after, in case the event is
  /// just behind).
  void _scheduleNowPlayingCheck() {
    if (_npCheck?.isActive ?? false) {
      return;
    }
    _npCheck = Timer(const Duration(milliseconds: 800), () {
      final active = state.activeRoomId;
      if (active == null || _disposed) {
        return;
      }
      final room = state.rooms.where((r) => r.id == active).firstOrNull;
      final np = state.nowPlayingByRoom[active];
      if (room != null && (np == null || np.title != room.nowTitle || np.imageUrl != room.nowImageUrl)) {
        unawaited(_refreshNowPlaying(active));
      }
    });
  }

  Future<void> _refreshNowPlaying(String roomId) async {
    final client = _client;
    if (client == null) {
      return;
    }
    final askedAt = DateTime.now();
    try {
      final read = await client.nowPlaying(roomId).timeout(const Duration(seconds: 4));
      if (_disposed || !identical(client, _client) || state.activeRoomId != roomId) {
        return; // user moved on: don't paint an old room's song
      }
      final np = _notOlderThanCommand(read, askedAt);
      _confirmPlayHold(np, askedAt);
      state = state.copyWith(nowPlaying: np, nowPlayingByRoom: {...state.nowPlayingByRoom, roomId: np});
    } on Object {
      // The event stream or the next heartbeat will catch up.
    }
  }

  Future<void> togglePlayPause() async {
    final room = state.activeRoom;
    final np = state.nowPlaying;
    if (room == null || np == null) {
      return;
    }
    final shouldPause = np.isPlaying;
    if (state.mockMode) {
      final next = np.copyWith(state: shouldPause ? 'paused' : 'playing');
      _mock.nowPlaying[room.id] = next;
      state = state.copyWith(nowPlaying: next);
      return;
    }
    // Explicit play or pause from what the user saw, never a toggle: if the
    // screen was stale (just woke up), "pause" on a paused player is a no-op
    // instead of starting it again.
    final wanted = shouldPause ? 'paused' : 'playing';
    final now = DateTime.now();
    _playHolds[room.id] = (state: wanted, until: now.add(playHoldMax), since: now);
    _lastPlayCommand[room.id] = (state: wanted, at: now);
    state = state; // shows the new play/pause state at once
    _playInFlight[room.id] = (_playInFlight[room.id] ?? 0) + 1;
    try {
      await _transport(room.id, shouldPause ? 'pause' : 'play', retryLink: true);
      final h = _playHolds[room.id];
      final after = DateTime.now().add(playHoldAfterReply);
      if (h != null && h.state == wanted && h.until.isBefore(after)) {
        _playHolds[room.id] = (state: h.state, until: after, since: h.since);
      }
    } on Object {
      if (_playHolds[room.id]?.state == wanted) {
        _playHolds.remove(room.id);
        _lastPlayCommand.remove(room.id);
      }
      state = state;
      rethrow;
    } finally {
      final left = (_playInFlight[room.id] ?? 1) - 1;
      if (left <= 0) {
        _playInFlight.remove(room.id);
      } else {
        _playInFlight[room.id] = left;
      }
      _refreshSoon(room.id);
    }
  }

  /// A report about [np] read at [readAt]: if it was read after the last
  /// play/pause command and agrees with it, the player has caught up and the
  /// hold can go. Reports read before the command never end it.
  /// A fetch that left before the last play/pause command can't undo it,
  /// even once the hold has ended.
  NowPlaying _notOlderThanCommand(NowPlaying np, DateTime askedAt) {
    final cmd = _lastPlayCommand[np.playerId];
    if (cmd == null || !askedAt.isBefore(cmd.at) || np.state == cmd.state) {
      return np;
    }
    if (np.source == null || np.source == 'none') {
      return np;
    }
    return np.copyWith(state: cmd.state);
  }

  final _lastPlayCommand = <String, ({String state, DateTime at})>{};

  void _confirmPlayHold(NowPlaying np, DateTime readAt) {
    final h = _playHolds[np.playerId];
    if (h != null && np.state == h.state && !readAt.isBefore(h.since)) {
      _playHolds.remove(np.playerId);
    }
  }

  /// After a control, read now playing back quickly instead of waiting for
  /// the next event or poll.
  void _refreshSoon(String roomId) {
    for (final ms in const [350, 1500]) {
      Timer(Duration(milliseconds: ms), () {
        if (!_disposed && state.activeRoomId == roomId) {
          unawaited(_refreshNowPlaying(roomId));
        }
      });
    }
  }

  Future<void> skipNext() async {
    final room = state.activeRoom;
    if (room == null) {
      return;
    }
    if (state.mockMode) {
      final current = state.nowPlaying;
      if (current != null) {
        final next = current.copyWith(title: '${current.title ?? 'Track'} (next)');
        _mock.nowPlaying[room.id] = next;
        state = state.copyWith(nowPlaying: next);
      }
      return;
    }
    await _transport(room.id, 'next');
    _refreshSoon(room.id);
  }

  /// Remove an offline room from the home (it reappears if it comes back).
  Future<void> forgetRoom(String roomId) async {
    if (state.mockMode) {
      _mock.rooms.removeWhere((r) => r.id == roomId);
      state = state.copyWith(rooms: List.of(_mock.rooms));
      return;
    }
    final client = _client;
    if (client == null) {
      return;
    }
    await client.forgetRoom(roomId);
    state = state.copyWith(rooms: state.rooms.where((r) => r.id != roomId).toList());
  }

  Future<void> setVolume(String roomId, int level) async {
    final limit = state.settings[roomId]?.volumeLimit ?? 100;
    final clamped = level.clamp(0, limit);
    if (state.mockMode) {
      _mock.rooms = [
        for (final room in _mock.rooms)
          if (room.id == roomId) room.copyWith(volume: clamped) else room,
      ];
      state = state.copyWith(rooms: List.of(_mock.rooms));
      return;
    }
    await _client?.setVolume(roomId, clamped);
  }

  Future<void> skipPrevious() async {
    final room = state.activeRoom;
    if (room == null || state.mockMode) {
      return;
    }
    await _transport(room.id, 'previous');
    _refreshSoon(room.id);
  }

  /// Volume for the whole group the room is in (every room moves together).
  Future<void> setGroupVolume(String roomId, int level) async {
    if (state.mockMode) {
      final room = state.rooms.where((r) => r.id == roomId).firstOrNull;
      for (final id in room?.memberIds.isNotEmpty == true ? room!.memberIds : [roomId]) {
        await setVolume(id, level);
      }
      return;
    }
    await _client?.setGroupVolume(roomId, level.clamp(0, 100));
  }

  /// Make [roomId]'s group exactly [memberIds] (roomId's group leads).
  Future<void> setGroupMembers(String roomId, List<String> memberIds) async {
    final room = state.rooms.where((r) => r.id == roomId).firstOrNull;
    if (room == null) {
      return;
    }
    if (state.mockMode) {
      _mock.setGroup(roomId, memberIds);
      state = state.copyWith(rooms: List.of(_mock.rooms));
      return;
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a player');
    }
    await client.setGroupMembers(room.groupId ?? roomId, memberIds);
    await _reloadRooms();
  }

  /// Take one room out of its group.
  Future<void> leaveGroup(String roomId) async {
    if (state.mockMode) {
      _mock.ungroup(roomId);
      state = state.copyWith(rooms: List.of(_mock.rooms));
      return;
    }
    final room = state.rooms.where((r) => r.id == roomId).firstOrNull;
    await _client?.leaveGroup(room?.groupId ?? roomId, roomId);
    await _reloadRooms();
  }

  Future<void> _reloadRooms() async {
    final client = _client;
    if (client == null) {
      return;
    }
    try {
      final rooms = await client.listRooms().timeout(const Duration(seconds: 5));
      state = state.copyWith(rooms: rooms);
    } on Object {
      // The event stream brings the new rooms shortly anyway.
    }
  }

  Future<List<RadioStation>> searchRadio(String query, {String? tag, String? country}) async {
    if (state.mockMode) {
      return [
        for (final s in _mock.radioStations)
          if ((query.isEmpty || s.name.toLowerCase().contains(query.toLowerCase())) &&
              (tag == null || (s.tags ?? '').contains(tag)) &&
              (country == null || s.countrycode == country))
            s,
      ];
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a player');
    }
    return client.searchRadio(query: query, tag: tag, country: country);
  }

  Future<List<RadioStation>> recentRadio() async {
    if (state.mockMode) {
      return const [];
    }
    try {
      return await _client?.recentRadio() ?? const [];
    } on Object {
      return const [];
    }
  }

  /// Play [station] in the active room (and every room grouped with it).
  Future<void> playRadio(RadioStation station, {String? roomId}) async {
    final target = roomId ?? state.activeRoom?.id;
    if (target == null) {
      throw ControlApiException('Pick a room first');
    }
    if (state.mockMode) {
      final np = NowPlaying(
        playerId: target,
        title: station.name,
        stationName: station.name,
        imageUrl: station.favicon,
        state: 'playing',
        source: 'radio',
      );
      _mock.nowPlaying[target] = np;
      state = state.copyWith(nowPlaying: target == state.activeRoomId ? np : null);
      return;
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a player');
    }
    await client.playRadio(target, station);
  }

  // ---- music library and podcasts ------------------------------------------
  // Played by the room's group leader through MPD into the group's stream, so
  // they go through each room's sound settings like every other source.

  ControlApiClient _needClient() {
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a player');
    }
    return client;
  }

  String _needRoom(String? roomId) {
    final target = roomId ?? state.activeRoom?.id;
    if (target == null) {
      throw ControlApiException('Pick a room first');
    }
    return target;
  }

  void _demoPlay(String roomId, NowPlaying np) {
    _mock.nowPlaying[roomId] = np;
    state = state.copyWith(nowPlaying: roomId == state.activeRoomId ? np : null);
  }

  Future<LibrarySummary> librarySummary() async =>
      state.mockMode ? demoLibrarySummary : _needClient().librarySummary();

  Future<LibraryShare> addLibraryShare(Map<String, dynamic> body) async {
    if (state.mockMode) {
      throw ControlApiException('Connect to a player to add a music library');
    }
    final player = state.activeRoom?.id;
    return _needClient().addLibraryShare({...body, 'player_id': ?player});
  }

  Future<void> removeLibraryShare(String id) async => state.mockMode ? null : _needClient().removeLibraryShare(id);

  Future<void> rescanLibrary() async => state.mockMode ? null : _needClient().rescanLibrary();

  Future<List<UsbDrive>> usbDrives() async => state.mockMode ? const [] : _needClient().usbDrives();

  Future<List<String>> libraryArtists() async =>
      state.mockMode ? {for (final a in demoAlbums) a.artist!}.toList() : _needClient().libraryArtists();

  Future<List<LibraryAlbum>> libraryAlbums({String? artist}) async => state.mockMode
      ? [for (final a in demoAlbums) if (artist == null || a.artist == artist) a]
      : _needClient().libraryAlbums(artist: artist);

  Future<List<LibraryTrack>> libraryAlbum(String album, {String? artist}) async => state.mockMode
      ? [for (final t in demoTracks) if (t.album == album) t]
      : _needClient().libraryAlbum(album, artist: artist);

  Future<LibraryListing> libraryFolder(String path) async {
    if (!state.mockMode) {
      return _needClient().libraryFolder(path);
    }
    return path.isEmpty
        ? LibraryListing(folders: [for (final a in demoAlbums) LibraryFolder(path: a.album, name: a.album)])
        : LibraryListing(path: path, tracks: [for (final t in demoTracks) if (t.album == path) t]);
  }

  Future<LibrarySearchResult> librarySearch(String query) async {
    if (!state.mockMode) {
      return _needClient().librarySearch(query);
    }
    final q = query.toLowerCase();
    bool hit(String? v) => v != null && v.toLowerCase().contains(q);
    return LibrarySearchResult(
      artists: {for (final a in demoAlbums) if (hit(a.artist)) a.artist!}.toList(),
      albums: [for (final a in demoAlbums) if (hit(a.album) || hit(a.artist)) a],
      tracks: [for (final t in demoTracks) if (hit(t.title) || hit(t.artist) || hit(t.album)) t],
    );
  }

  /// Cover for an album or a file; null in the demo (the logo shows).
  String? libraryArtUrl({String? uri, String? album, String? artist}) =>
      state.mockMode ? null : _client?.libraryArtUrl(uri: uri, album: album, artist: artist);

  Future<void> playLibrary(List<LibraryTrack> tracks, {int start = 0, String? contextName, String? roomId}) async {
    final target = _needRoom(roomId);
    if (state.mockMode) {
      final t = tracks[start.clamp(0, tracks.length - 1)];
      _demoPlay(
        target,
        NowPlaying(
          playerId: target,
          title: t.title,
          artist: t.artist,
          album: t.album,
          duration: t.duration,
          position: 0,
          state: 'playing',
          source: 'library',
        ),
      );
      return;
    }
    await _needClient().playLibrary(target, tracks, start: start, contextName: contextName);
    _refreshSoon(target);
  }

  Future<List<Podcast>> searchPodcasts(String query) async {
    if (!state.mockMode) {
      return _needClient().searchPodcasts(query);
    }
    return [for (final p in demoPodcasts) if (p.title.toLowerCase().contains(query.toLowerCase())) p];
  }

  Future<List<Podcast>> podcastSubscriptions() async =>
      state.mockMode ? List.of(_mock.podcastSubs) : _needClient().podcastSubscriptions();

  Future<void> subscribePodcast(Podcast podcast) async {
    if (state.mockMode) {
      _mock.podcastSubs.removeWhere((p) => p.feed == podcast.feed);
      _mock.podcastSubs.insert(0, podcast);
      return;
    }
    await _needClient().subscribePodcast(podcast);
  }

  Future<void> unsubscribePodcast(String feed) async {
    if (state.mockMode) {
      _mock.podcastSubs.removeWhere((p) => p.feed == feed);
      return;
    }
    await _needClient().unsubscribePodcast(feed);
  }

  Future<PodcastFeed> podcastFeed(Podcast podcast, {bool fresh = false}) async {
    if (!state.mockMode) {
      return _needClient().podcastFeed(podcast.feed, fresh: fresh);
    }
    return PodcastFeed(
      podcast: podcast,
      description: 'A demo podcast.',
      subscribed: _mock.podcastSubs.any((p) => p.feed == podcast.feed),
      episodes: demoEpisodes,
    );
  }

  /// Plays [episode]; resumes where anyone in the home left off unless
  /// [fromStart].
  Future<void> playPodcast(PodcastEpisode episode, Podcast podcast, {bool fromStart = false, String? roomId}) async {
    final target = _needRoom(roomId);
    if (state.mockMode) {
      _demoPlay(
        target,
        NowPlaying(
          playerId: target,
          title: episode.title,
          artist: podcast.title,
          imageUrl: episode.image ?? podcast.image,
          duration: episode.duration,
          position: fromStart ? 0 : (episode.canResume ? episode.position : 0),
          state: 'playing',
          source: 'podcast',
        ),
      );
      return;
    }
    await _needClient().playPodcast(target, episode, podcast, position: fromStart ? 0 : null);
    _refreshSoon(target);
  }

  /// Demo mode: pretend [roomId]'s group started playing [np].
  void showDemoNowPlaying(String roomId, NowPlaying np) {
    _mock.nowPlaying[roomId] = np;
    final byRoom = Map<String, NowPlaying>.of(state.nowPlayingByRoom)..[roomId] = np;
    state = state.copyWith(
      nowPlayingByRoom: byRoom,
      nowPlaying: roomId == state.activeRoomId ? np : null,
    );
  }

  /// Now-playing for any room: live event data, else the rooms-list summary.
  NowPlaying? nowPlayingFor(String roomId) {
    if (roomId == state.activeRoomId && state.nowPlaying != null) {
      return state.nowPlaying;
    }
    if (state.mockMode) {
      return _mock.nowPlaying[roomId];
    }
    return state.nowPlayingByRoom[roomId];
  }

  /// Seek Spotify playback in the active room's group (seconds).
  Future<void> seek(double seconds) async {
    final room = state.activeRoom;
    final np = state.nowPlaying;
    if (room == null || np == null) {
      return;
    }
    final updated = np.copyWith(position: seconds, receivedAt: DateTime.now());
    state = state.copyWith(nowPlaying: updated);
    if (state.mockMode) {
      _mock.nowPlaying[room.id] = updated;
      return;
    }
    await _transport(room.id, 'seek', positionMs: (seconds * 1000).round());
  }

  /// Player first; for Spotify, the Web API when the player can't.
  Future<void> _transport(String roomId, String action, {int? positionMs, bool retryLink = false}) async {
    final client = _client;
    if (client == null) {
      return;
    }
    try {
      if (retryLink) {
        // Play and pause are idempotent: right after a wake the first request
        // can die on a socket iOS closed while suspended, so try once more.
        try {
          await _transportOnce(client, roomId, action, positionMs);
        } on Object catch (e) {
          if (e is ControlApiException && e.statusCode != null) {
            rethrow; // the player answered: a real error, not a dead socket
          }
          await Future<void>.delayed(const Duration(milliseconds: 250));
          await _transportOnce(client, roomId, action, positionMs);
        }
        return;
      }
      await _transportOnce(client, roomId, action, positionMs);
    } on Object {
      final source = nowPlayingFor(roomId)?.source ?? state.activeRoom?.source;
      if (source != 'spotify' || !ref.read(spotifyProvider).signedIn) {
        rethrow;
      }
      await ref.read(spotifyProvider.notifier).webControl(roomId, action, positionMs: positionMs);
    }
  }

  Future<void> _transportOnce(ControlApiClient client, String roomId, String action, int? positionMs) async {
    switch (action) {
      case 'play':
        await client.play(roomId);
      case 'pause':
        await client.pause(roomId);
      case 'next':
        await client.next(roomId);
      case 'previous':
        await client.previous(roomId);
      case 'seek':
        await client.seek(roomId, positionMs ?? 0);
    }
  }

  /// After BLE provisioning in demo mode, surface the player as unclaimed.
  void rememberProvisionedPlayer({
    required String id,
    required String name,
    String? address,
  }) {
    if (!state.mockMode) {
      return;
    }
    _mock.offerProvisioned(id: id, name: name, address: address);
  }

  Future<List<UnclaimedPlayer>> listUnclaimed() async {
    if (state.mockMode) {
      return List.of(_mock.unclaimed);
    }
    final client = _client;
    if (client == null) {
      return const [];
    }
    return client.listUnclaimed();
  }

  Future<void> adoptPlayer({required String name, String? id, String? url}) async {
    if (state.mockMode) {
      _mock.adopt(name: name, id: id, url: url);
      state = state.copyWith(
        rooms: List.of(_mock.rooms),
        settings: Map.of(_mock.settings),
      );
      return;
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a system');
    }
    await client.adoptPlayer(name: name, id: id, url: url);
    final rooms = await client.listRooms();
    state = state.copyWith(rooms: rooms);
  }

  /// Add a board that has just joined Wi-Fi (or was found on the LAN).
  ///
  /// With no system yet, or when the board is the very host the app is
  /// connected to, it becomes the household host (create). Otherwise it joins
  /// the connected system as a room. Failures are [AdoptFailure]s naming the
  /// step and the precise reason.
  Future<void> adoptProvisioned({
    required String name,
    String? id,
    String? ip,
    String? url,
    AdoptProgress? onProgress,
    bool allowNewHome = false,
    bool resetFirst = false,
  }) async {
    if (state.mockMode) {
      await adoptPlayer(name: name, id: id, url: url);
      return;
    }
    final address = provisionedIp(ip: ip, url: url);
    if (_client == null && !allowNewHome) {
      // A home was saved but the phone is not connected to it right now.
      // Never quietly make the new board a separate home: look for the home
      // again (saved host, its last known members, mDNS), or explain.
      final prefs = ref.read(connectionPrefsProvider);
      final known = state.hostUrl ?? prefs?.savedHost;
      if (known != null || (prefs?.knownMembers.isNotEmpty ?? false)) {
        onProgress?.call('Looking for your home…');
        final seen = await _reconnectToHome(excludeHost: address);
        if (_client == null) {
          if (seen) {
            throw AdoptFailure(
              'Reaching your home',
              'Players of your home are on the network but did not answer (last host $known)',
              url: known,
              recovery: AdoptRecovery.startNewHome,
              customHint: 'Your phone can\'t reach your other players right now. Make sure it is on the same Wi-Fi '
                  'and try again. (Adding this one now would start a separate home.)',
            );
          }
          throw AdoptFailure(
            'Reaching your home',
            'No player of the saved home answered or was found on this network (last host $known)',
            url: known,
            recovery: AdoptRecovery.forgetOldHome,
            customHint: 'Your previous home isn\'t on this network (were its players reset or reflashed?). '
                'You can set up a new home with this player.',
          );
        }
      }
    }
    if (allowNewHome && _client == null) {
      // The user chose to start over: drop the old home so the app never
      // tries to go back to it.
      await ref.read(connectionPrefsProvider)?.forgetHome();
      state = state.copyWith(clearHostUrl: true, rooms: const [], settings: const {});
    }
    final client = _client;
    if (resetFirst && client != null && address != null) {
      final setup = ref.read(firstPlayerSetupProvider)();
      try {
        await setup.resetPlayer(ip: address, onProgress: onProgress);
      } finally {
        setup.close();
      }
    }
    final connectedHost = client == null ? null : Uri.tryParse(client.baseUrl)?.host;
    final createSystem = client == null || (address != null && address == connectedHost);
    if (!createSystem) {
      var playerId = id;
      if (address != null && !resetFirst) {
        // Phone → new board first: is it up yet, and is it really unclaimed?
        onProgress?.call('Waiting for $name to come online…');
        final setup = ref.read(firstPlayerSetupProvider)();
        Map<String, dynamic>? info;
        try {
          info = await setup.probe(address, wait: probeWait);
        } finally {
          setup.close();
        }
        if (info != null) {
          playerId = info['id'] as String? ?? playerId;
          final theirs = (info['household'] as Map?)?['id'];
          if (info['state'] == 'ready' && theirs != null && _householdId != null && theirs != _householdId) {
            throw AdoptFailure(
              'Adding it to your home',
              'The player at $address is already set up in another home',
              statusCode: 409,
              url: 'http://$address:8080/api/v2/info',
              recovery: AdoptRecovery.moveHere,
            );
          }
          if (info['state'] == 'ready' && theirs != null && _householdId != null && theirs == _householdId) {
            // Already one of ours (a retry after a lost reply): just refresh.
            await _refreshRoomsAfterAdopt(client);
            return;
          }
        }
      }
      onProgress?.call('Adding $name to your system…');
      await _joinViaAnyPlayer(client, name: name, id: playerId, url: url, ip: address);
      await _refreshRoomsAfterAdopt(client);
      return;
    }
    if (address == null) {
      throw AdoptFailure('Reaching the player', 'The player did not report its network address');
    }
    final setup = ref.read(firstPlayerSetupProvider)();
    final String base;
    try {
      base = await setup.createSystem(ip: address, name: name, onProgress: onProgress);
    } finally {
      setup.close();
    }
    onProgress?.call('Connecting to your system…');
    for (var attempt = 0; attempt < 5; attempt++) {
      if (await connectTo(base, persist: true)) {
        return;
      }
      await Future<void>.delayed(const Duration(seconds: 2));
    }
    throw AdoptFailure(
      'Connecting to your system',
      state.error ?? 'No answer from $base',
      url: '$base/api/v2/rooms',
    );
  }

  /// Find the saved home again: the saved host, then its last known members,
  /// then players seen over mDNS that belong to the same household (or, if
  /// the household id is unknown, any set-up player). Connects to the first
  /// that answers. Returns whether any player of that home was *seen* (so a
  /// failure means "not reachable" rather than "gone").
  Future<bool> _reconnectToHome({String? excludeHost}) async {
    final prefs = ref.read(connectionPrefsProvider);
    final savedHh = prefs?.householdId;
    final candidates = <String>{
      ?state.hostUrl,
      ?prefs?.savedHost,
      ...?prefs?.knownMembers,
      for (final room in state.rooms) ?room.url,
    };
    final hosts = await _safeDiscover(ref.read(appConfigProvider).discoveryTimeout);
    state = state.copyWith(discoveredHosts: hosts);
    var seen = false;
    for (final h in hosts) {
      final sameHome = savedHh != null ? h.household == savedHh : !h.unclaimed;
      if (sameHome && !h.unclaimed && Uri.tryParse(h.url)?.host != excludeHost) {
        seen = true;
        candidates.add(h.url);
      }
    }
    for (final url in candidates) {
      if (Uri.tryParse(url)?.host == excludeHost) {
        continue;
      }
      if (await _canReach(url) && await connectTo(url, persist: true)) {
        return true;
      }
    }
    return seen;
  }

  /// Remember which household the connected player belongs to and where its
  /// members are. A different household than the saved one (the old home was
  /// reset, or the user picked another system) replaces it cleanly.
  Future<void> _rememberHome(ControlApiClient client) async {
    final hh = _householdId;
    if (hh == null || !identical(client, _client)) {
      return;
    }
    final prefs = ref.read(connectionPrefsProvider);
    final previous = prefs?.householdId;
    if (previous != null && previous != hh) {
      // Different home: drop cached per-room settings from the old one.
      state = state.copyWith(settings: const {});
    }
    final members = <String>{
      client.baseUrl,
      for (final room in state.rooms) ?room.url,
    }.toList();
    await prefs?.setHousehold(hh, members: members);
  }

  /// How long the phone waits for a just-provisioned board to answer.
  Duration probeWait = const Duration(seconds: 20);

  /// Ask the connected player to adopt; if *it* can't reach the new board,
  /// ask the other players in the home in turn (any member can adopt).
  Future<void> _joinViaAnyPlayer(
    ControlApiClient client, {
    required String name,
    String? id,
    String? url,
    String? ip,
  }) async {
    try {
      await joinExistingSystem(client, name: name, id: id, url: url, ip: ip);
      return;
    } on AdoptFailure catch (err) {
      final unreachable = err.statusCode == 502 || err.statusCode == 504 || err.statusCode == null;
      if (!unreachable) {
        rethrow;
      }
      final connectedHost = Uri.tryParse(client.baseUrl)?.host;
      final others = <String>{
        for (final room in state.rooms)
          if (room.url != null && room.available && Uri.tryParse(room.url!)?.host != connectedHost) room.url!,
      };
      for (final other in others) {
        final via = ref.read(controlClientFactoryProvider)(other);
        try {
          await joinExistingSystem(via, name: name, id: id, url: url, ip: ip);
          return;
        } on AdoptFailure {
          // try the next player
        } finally {
          await via.close();
        }
      }
      throw AdoptFailure(
        err.step,
        err.reason,
        statusCode: err.statusCode,
        url: err.url,
        customHint: 'Your players could not reach the new one${ip == null ? '' : ' at $ip'}. '
            'Make sure it joined the same Wi-Fi as your other players (not a guest network), then try again.',
      );
    }
  }

  Future<void> _refreshRoomsAfterAdopt(ControlApiClient client) async {
    try {
      final rooms = await client.listRooms().timeout(const Duration(seconds: 5));
      state = state.copyWith(rooms: rooms);
      await _rememberHome(client);
    } on Object {
      // The new room arrives over the event stream once the players see each other.
    }
    await refreshHealth();
  }

  /// Returns a one-line result for the room settings sheet.
  Future<String> testTone(String playerId) async {
    if (state.mockMode) {
      await Future<void>.delayed(const Duration(milliseconds: 300));
      return 'Test tone played (demo)';
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a system');
    }
    final result = await client.testTone(playerId);
    return describeToneResult(result);
  }

  /// Restart a player; it drops off for about a minute.
  Future<void> rebootPlayer(String playerId) async {
    if (state.mockMode) {
      return;
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a system');
    }
    await client.rebootPlayer(playerId);
  }

  /// Factory-reset a player from the app (same routine as the power-button reset).
  Future<void> factoryResetPlayer(String playerId) async {
    if (state.mockMode) {
      return;
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a system');
    }
    await client.factoryResetPlayer(playerId);
  }

  Future<Map<String, dynamic>> roomAudio(String playerId) async {
    if (state.mockMode) {
      return const {
        'output': {'pcm': 'hdmi:CARD=vc4hdmi0,DEV=0', 'kind': 'hdmi', 'reason': 'HDMI0 (display connected)'},
        'chain': {'dsp': 'live', 'ok': true},
        'snapclient': {'connected': true, 'volume': 40, 'muted': false, 'stream': 'Radio'},
      };
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a system');
    }
    return client.roomAudio(playerId);
  }

  Future<PlayerSettings> loadSettings(String playerId) async {
    if (state.mockMode) {
      final existing = _mock.settings[playerId] ??
          PlayerSettings(name: _mock.rooms.where((r) => r.id == playerId).firstOrNull?.name ?? playerId);
      final next = Map<String, PlayerSettings>.of(state.settings)..[playerId] = existing;
      state = state.copyWith(settings: next);
      return existing;
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a system');
    }
    final loaded = await client.getSettings(playerId);
    final next = Map<String, PlayerSettings>.of(state.settings)..[playerId] = loaded;
    state = state.copyWith(settings: next);
    return loaded;
  }

  Future<PlayerSettings> saveSettings(String playerId, PlayerSettings settings) async {
    if (state.mockMode) {
      _mock.settings[playerId] = settings;
      _mock.rooms = [
        for (final room in _mock.rooms)
          if (room.id == playerId) room.copyWith(name: settings.name) else room,
      ];
      final next = Map<String, PlayerSettings>.of(state.settings)..[playerId] = settings;
      state = state.copyWith(rooms: List.of(_mock.rooms), settings: next);
      return settings;
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a system');
    }
    final saved = await client.patchSettings(playerId, settings.toPatch());
    final rooms = await client.listRooms();
    final next = Map<String, PlayerSettings>.of(state.settings)..[playerId] = saved;
    state = state.copyWith(rooms: rooms, settings: next);
    return saved;
  }

  Future<SshDeveloperStatus> loadSsh(String playerId) async {
    if (state.mockMode) {
      return SshDeveloperStatus(
        enabled: false,
        hostname: 'freesounder-demo',
        command: 'ssh developer@freesounder-demo.local',
        warning: 'Demo mode — SSH is not really enabled on a player.',
      );
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a system');
    }
    return client.getSsh(playerId);
  }

  Future<SshDeveloperStatus> setSsh(
    String playerId, {
    required bool enabled,
    String? publicKey,
  }) async {
    if (state.mockMode) {
      if (!enabled) {
        return SshDeveloperStatus(
          enabled: false,
          hostname: 'freesounder-demo',
          command: 'ssh developer@freesounder-demo.local',
        );
      }
      return const SshDeveloperStatus(
        enabled: true,
        hostname: 'freesounder-demo',
        command: 'ssh developer@freesounder-demo.local',
        password: 'demo-ssh-password',
        warning: 'Demo mode — this password is only an example.',
      );
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a system');
    }
    return client.setSsh(playerId, enabled: enabled, confirm: true, publicKey: publicKey);
  }
}

String? _normaliseUrl(String raw) {
  var value = raw.trim();
  if (value.isEmpty) {
    return null;
  }
  if (!value.contains('://')) {
    value = 'http://$value';
  }
  final uri = Uri.tryParse(value);
  if (uri == null || uri.host.isEmpty) {
    return null;
  }
  if (!uri.hasPort) {
    return uri.replace(port: 8080).toString().replaceFirst(RegExp(r'/$'), '');
  }
  return value.replaceFirst(RegExp(r'/$'), '');
}

/// "Played on HDMI0 (display connected)" or the failure reason.
String describeToneResult(Map<String, dynamic> result) {
  final audio = (result['audio'] as Map?)?.cast<String, dynamic>() ?? const {};
  final output = (audio['output'] as Map?)?.cast<String, dynamic>() ?? const {};
  final where = (output['reason'] as String?)?.isNotEmpty == true ? output['reason'] as String : output['pcm'] ?? 'the speaker';
  if (result['ok'] == true) {
    if (output['kind'] == 'null') {
      return 'No speaker found: this Pi has no audio board and no HDMI display with sound connected.';
    }
    return 'Test tone played on $where. If you heard nothing, check the TV or amplifier input and volume.';
  }
  return 'Test tone failed on $where: ${result['detail'] ?? 'unknown error'}';
}

/// Short lines for the room settings "Audio output" panel.
List<String> describeRoomAudio(Map<String, dynamic> audio) {
  final output = (audio['output'] as Map?)?.cast<String, dynamic>() ?? const {};
  final chain = (audio['chain'] as Map?)?.cast<String, dynamic>();
  final hw = (audio['hw'] as Map?)?.cast<String, dynamic>();
  final snap = (audio['snapclient'] as Map?)?.cast<String, dynamic>();
  final stream = (audio['stream'] as Map?)?.cast<String, dynamic>();
  return [
    'Output: ${output['reason'] ?? output['pcm'] ?? 'unknown'}',
    if (output['pcm'] != null) 'Device: ${output['pcm']}${hw != null ? ' (${hw['state'] ?? '?'})' : ''}',
    if ((output['detected'] as String?)?.isNotEmpty ?? false) 'Detected: ${output['detected']}',
    if ((output['cards'] as String?)?.isNotEmpty ?? false) 'Sound cards: ${output['cards']}',
    if ((output['check'] as String?)?.isNotEmpty ?? false)
      output['check'] == 'ok' ? 'Output check: OK' : 'Output check: ${output['check']}',
    if (output['direct'] == '1') 'Tone controls: off (the full sound chain would not open on this device)',
    if (chain != null)
      chain['ok'] == false
          ? 'Sound chain: FAILED (${chain['detail']})'
          : 'Sound chain: OK${chain['dsp'] == 'fallback' ? ', tone controls unavailable' : ''}',
    if (snap != null)
      'Player: ${snap['connected'] == true ? 'connected' : 'not connected'}, volume ${snap['volume'] ?? '?'}${snap['muted'] == true ? ', muted' : ''}',
    if (stream != null) 'Stream: ${stream['id']} ${stream['status'] ?? ''}'.trim(),
  ];
}
