import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/connection_prefs.dart';
import '../api/control_client.dart';
import '../api/discovery.dart';
import '../api/first_player.dart';
import '../api/mock_data.dart';
import '../models/now_playing.dart';
import '../spotify/spotify_store.dart';
import '../models/player_settings.dart';
import '../models/radio_station.dart';
import '../models/room.dart';

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
  }) {
    return PlaybackState(
      rooms: rooms ?? this.rooms,
      activeRoomId: activeRoomId ?? this.activeRoomId,
      nowPlaying: nowPlaying ?? this.nowPlaying,
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

/// Overridable in tests.
final firstPlayerSetupProvider = Provider<FirstPlayerSetup Function()>((ref) => FirstPlayerSetup.new);

/// Single rooms-and-playback store. Every screen reads this; it is the source
/// of truth for the active room, groups, volumes, now-playing and sync health.
class PlaybackStore extends Notifier<PlaybackState> {
  ControlApiClient? _client;
  StreamSubscription<Map<String, dynamic>>? _events;
  Timer? _healthTimer;
  late MockHousehold _mock;

  ControlApiClient? get client => _client;
  MockHousehold get mockHousehold => _mock;

  @override
  PlaybackState build() {
    _mock = MockHousehold();
    final config = ref.watch(appConfigProvider);
    ref.onDispose(() {
      _healthTimer?.cancel();
      _events?.cancel();
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
      if (hosts.isEmpty) {
        hosts = await _safeDiscover(config.discoveryTimeout);
      }
      for (final host in hosts.where((h) => !h.unclaimed && h.url != base)) {
        if (await connectTo(host.url, persist: false)) {
          return;
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
      return await discoverControlApis(timeout: timeout);
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
      _events = client.connectEvents().listen(_onEvent, onError: (Object err) {
        state = state.copyWith(error: err.toString(), connected: false);
      });
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

  Future<void> _teardownClient() async {
    _healthTimer?.cancel();
    _healthTimer = null;
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
      state = state.copyWith(rooms: rooms, connected: true, clearError: true);
    } else if (event == 'now_playing' && data is Map<String, dynamic>) {
      final np = NowPlaying.fromJson(data);
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
    state = state.copyWith(activeRoomId: roomId);
    final client = _client;
    if (client != null) {
      client.nowPlaying(roomId).then((np) {
        state = state.copyWith(nowPlaying: np);
      });
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
    await _transport(room.id, shouldPause ? 'pause' : 'play');
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

  Future<List<RadioStation>> searchRadio(String query) async {
    if (state.mockMode) {
      return [
        for (final s in _mock.radioStations)
          if (query.isEmpty || s.name.toLowerCase().contains(query.toLowerCase())) s,
      ];
    }
    final client = _client;
    if (client == null) {
      throw ControlApiException('Not connected to a player');
    }
    return client.searchRadio(query: query);
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
  Future<void> _transport(String roomId, String action, {int? positionMs}) async {
    final client = _client;
    if (client == null) {
      return;
    }
    try {
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
    } on Object {
      final source = nowPlayingFor(roomId)?.source ?? state.activeRoom?.source;
      if (source != 'spotify' || !ref.read(spotifyProvider).signedIn) {
        rethrow;
      }
      await ref.read(spotifyProvider.notifier).webControl(roomId, action, positionMs: positionMs);
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
  }) async {
    if (state.mockMode) {
      await adoptPlayer(name: name, id: id, url: url);
      return;
    }
    final client = _client;
    final address = provisionedIp(ip: ip, url: url);
    final connectedHost = client == null ? null : Uri.tryParse(client.baseUrl)?.host;
    final createSystem = client == null || (address != null && address == connectedHost);
    if (!createSystem) {
      onProgress?.call('Adding $name to your system…');
      await joinExistingSystem(client, name: name, id: id, url: url, ip: address);
      try {
        final rooms = await client.listRooms().timeout(const Duration(seconds: 5));
        state = state.copyWith(rooms: rooms);
      } on Object {
        // The new room arrives over the event stream once the players see each other.
      }
      await refreshHealth();
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
    if (chain != null)
      chain['ok'] == false
          ? 'Sound chain: FAILED (${chain['detail']})'
          : 'Sound chain: OK${chain['dsp'] == 'fallback' ? ', tone controls unavailable' : ''}',
    if (snap != null)
      'Player: ${snap['connected'] == true ? 'connected' : 'not connected'}, volume ${snap['volume'] ?? '?'}${snap['muted'] == true ? ', muted' : ''}',
    if (stream != null) 'Stream: ${stream['id']} ${stream['status'] ?? ''}'.trim(),
  ];
}
