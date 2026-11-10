import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/connection_prefs.dart';
import '../api/control_client.dart';
import '../api/discovery.dart';
import '../api/mock_data.dart';
import '../models/now_playing.dart';
import '../models/player_settings.dart';
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
  /// via mDNS (`_freesounder._tcp`) and the app falls back to demo mode.
  final String? apiBase;
  final Duration discoveryTimeout;
}

class PlaybackState {
  const PlaybackState({
    required this.rooms,
    this.activeRoomId,
    this.nowPlaying,
    this.mockMode = true,
    this.connected = false,
    this.discovering = false,
    this.error,
    this.hostUrl,
    this.discoveredHosts = const [],
    this.settings = const {},
  });

  final List<Room> rooms;
  final String? activeRoomId;
  final NowPlaying? nowPlaying;
  final bool mockMode;
  final bool connected;
  final bool discovering;
  final String? error;
  final String? hostUrl;
  final List<DiscoveredHost> discoveredHosts;
  final Map<String, PlayerSettings> settings;

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
    bool? mockMode,
    bool? connected,
    bool? discovering,
    String? error,
    bool clearError = false,
    String? hostUrl,
    bool clearHostUrl = false,
    List<DiscoveredHost>? discoveredHosts,
    Map<String, PlayerSettings>? settings,
  }) {
    return PlaybackState(
      rooms: rooms ?? this.rooms,
      activeRoomId: activeRoomId ?? this.activeRoomId,
      nowPlaying: nowPlaying ?? this.nowPlaying,
      mockMode: mockMode ?? this.mockMode,
      connected: connected ?? this.connected,
      discovering: discovering ?? this.discovering,
      error: clearError ? null : (error ?? this.error),
      hostUrl: clearHostUrl ? null : (hostUrl ?? this.hostUrl),
      discoveredHosts: discoveredHosts ?? this.discoveredHosts,
      settings: settings ?? this.settings,
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

/// Single rooms-and-playback store. Every screen reads this; it is the source
/// of truth for the active room, groups, volumes, now-playing and sync health.
class PlaybackStore extends Notifier<PlaybackState> {
  ControlApiClient? _client;
  StreamSubscription<Map<String, dynamic>>? _events;
  late MockHousehold _mock;

  @override
  PlaybackState build() {
    _mock = MockHousehold();
    final config = ref.watch(appConfigProvider);
    ref.onDispose(() {
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
      if (hosts.isNotEmpty) {
        base = hosts.first.url;
      }
    } else {
      unawaited(_refreshHosts());
    }
    if (base == null) {
      state = _demoState(hosts: hosts);
      return;
    }
    final ok = await connectTo(base, persist: false, allowDemoFallback: true);
    if (!ok) {
      if (hosts.isEmpty) {
        hosts = await _safeDiscover(config.discoveryTimeout);
      }
      if (hosts.isNotEmpty && hosts.first.url != base) {
        final retry = await connectTo(hosts.first.url, persist: false, allowDemoFallback: false);
        if (retry) {
          return;
        }
      }
      state = _demoState(
        error: 'Could not reach your system. Showing demo.',
        hosts: hosts,
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
    bool allowDemoFallback = false,
  }) async {
    final url = _normaliseUrl(rawUrl);
    if (url == null) {
      state = state.copyWith(error: 'Enter a host like 192.168.1.10 or http://host:8080');
      return false;
    }
    final previous = state;
    await _teardownClient();
    state = state.copyWith(discovering: true, clearError: true, hostUrl: url);
    final client = ControlApiClient(baseUrl: url);
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
      return true;
    } on Object catch (_) {
      await client.close();
      if (allowDemoFallback) {
        return false;
      }
      if (previous.mockMode || previous.rooms.isEmpty) {
        state = _demoState(
          error: 'Could not connect to $url. Showing demo.',
          hosts: previous.discoveredHosts,
        );
      } else {
        state = previous.copyWith(
          discovering: false,
          connected: false,
          error: 'Could not connect to $url',
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

  Future<void> _teardownClient() async {
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
      if (np.playerId == state.activeRoomId) {
        state = state.copyWith(nowPlaying: np);
      }
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
    if (shouldPause) {
      await _client?.pause(room.id);
    } else {
      await _client?.play(room.id);
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
    await _client?.next(room.id);
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
