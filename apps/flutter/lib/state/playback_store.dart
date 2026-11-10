import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/control_client.dart';
import '../api/discovery.dart';
import '../api/mock_data.dart';
import '../models/now_playing.dart';
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
  });

  final List<Room> rooms;
  final String? activeRoomId;
  final NowPlaying? nowPlaying;
  final bool mockMode;
  final bool connected;
  final bool discovering;
  final String? error;

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
  }) {
    return PlaybackState(
      rooms: rooms ?? this.rooms,
      activeRoomId: activeRoomId ?? this.activeRoomId,
      nowPlaying: nowPlaying ?? this.nowPlaying,
      mockMode: mockMode ?? this.mockMode,
      connected: connected ?? this.connected,
      discovering: discovering ?? this.discovering,
      error: clearError ? null : (error ?? this.error),
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

  PlaybackState _demoState() {
    return PlaybackState(
      rooms: List.of(_mock.rooms),
      activeRoomId: _mock.activeRoomId,
      nowPlaying: _mock.nowPlaying[_mock.activeRoomId],
      mockMode: true,
      discovering: false,
      connected: true,
    );
  }

  Future<void> _connectOrDemo() async {
    final config = ref.read(appConfigProvider);
    var base = config.apiBase;
    base ??= await discoverControlApi(timeout: config.discoveryTimeout);
    if (base == null) {
      state = _demoState();
      return;
    }
    _client = ControlApiClient(baseUrl: base);
    try {
      final rooms = await _client!.listRooms().timeout(const Duration(seconds: 3));
      final activeId = rooms.isNotEmpty ? rooms.first.id : null;
      NowPlaying? np;
      if (activeId != null) {
        np = await _client!.nowPlaying(activeId).timeout(const Duration(seconds: 3));
      }
      state = state.copyWith(
        rooms: rooms,
        activeRoomId: activeId,
        nowPlaying: np,
        mockMode: false,
        connected: true,
        discovering: false,
        clearError: true,
      );
      _events = _client!.connectEvents().listen(_onEvent, onError: (Object err) {
        state = state.copyWith(error: err.toString(), connected: false);
      });
    } on Object {
      // No reachable player — testers can still explore the UI in demo mode.
      state = _demoState();
    }
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
    if (state.mockMode) {
      _mock.rooms = [
        for (final room in _mock.rooms)
          if (room.id == roomId) room.copyWith(volume: level) else room,
      ];
      state = state.copyWith(rooms: List.of(_mock.rooms));
      return;
    }
    await _client?.setVolume(roomId, level);
  }
}
