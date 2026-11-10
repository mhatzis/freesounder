import '../models/now_playing.dart';
import '../models/room.dart';

/// In-app mock household so the UI runs without a player or Docker stack.
class MockHousehold {
  MockHousehold() {
    rooms = [
      const Room(id: 'kitchen', name: 'Kitchen', volume: 40, state: 'playing'),
      const Room(id: 'lounge', name: 'Lounge', volume: 30, state: 'idle'),
      const Room(id: 'bedroom', name: 'Bedroom', volume: 15, state: 'idle'),
    ];
    nowPlaying = const {
      'kitchen': NowPlaying(
        playerId: 'kitchen',
        title: 'Morning Light',
        artist: 'Example Artist',
        album: 'Demo Album',
        state: 'playing',
        position: 42,
        duration: 240,
      ),
      'lounge': NowPlaying(playerId: 'lounge'),
      'bedroom': NowPlaying(playerId: 'bedroom'),
    };
  }

  late List<Room> rooms;
  late Map<String, NowPlaying> nowPlaying;
  String activeRoomId = 'kitchen';
}
