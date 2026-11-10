import '../models/now_playing.dart';
import '../models/player_settings.dart';
import '../models/room.dart';

/// In-app mock household so the UI runs without a player or Docker stack.
class MockHousehold {
  MockHousehold() {
    rooms = [
      const Room(id: 'kitchen', name: 'Kitchen', volume: 40, state: 'playing'),
      const Room(id: 'lounge', name: 'Lounge', volume: 30, state: 'idle'),
      const Room(id: 'bedroom', name: 'Bedroom', volume: 15, state: 'idle'),
    ];
    nowPlaying = {
      'kitchen': const NowPlaying(
        playerId: 'kitchen',
        title: 'Morning Light',
        artist: 'Example Artist',
        album: 'Demo Album',
        state: 'playing',
        position: 42,
        duration: 240,
      ),
      'lounge': const NowPlaying(playerId: 'lounge'),
      'bedroom': const NowPlaying(playerId: 'bedroom'),
    };
    settings = {
      'kitchen': const PlayerSettings(name: 'Kitchen', bass: 2, treble: -1),
      'lounge': const PlayerSettings(name: 'Lounge', balance: -2, volumeLimit: 90),
      'bedroom': const PlayerSettings(name: 'Bedroom', loudness: true, volumeLimit: 80),
    };
    unclaimed = [
      const UnclaimedPlayer(
        id: 'attic-pi',
        name: 'freesounder-attic',
        url: 'http://192.168.1.42:8090',
        address: '192.168.1.42',
        port: 8090,
        role: 'room',
      ),
    ];
  }

  late List<Room> rooms;
  late Map<String, NowPlaying> nowPlaying;
  late Map<String, PlayerSettings> settings;
  late List<UnclaimedPlayer> unclaimed;
  String activeRoomId = 'kitchen';

  UnclaimedPlayer adopt({required String name, String? id, String? url}) {
    UnclaimedPlayer? found;
    final remaining = <UnclaimedPlayer>[];
    for (final player in unclaimed) {
      if (found == null && (player.id == id || (url != null && player.url == url))) {
        found = player;
      } else {
        remaining.add(player);
      }
    }
    unclaimed = remaining;
    final adopted = UnclaimedPlayer(
      id: found?.id ?? (id ?? name.toLowerCase().replaceAll(' ', '-')),
      name: name,
      url: found?.url ?? url ?? '',
      address: found?.address,
      port: found?.port,
      role: 'room',
      state: 'configured',
    );
    rooms = [
      ...rooms,
      Room(id: adopted.id, name: name, volume: 30, state: 'idle'),
    ];
    nowPlaying[adopted.id] = NowPlaying(playerId: adopted.id);
    settings[adopted.id] = PlayerSettings(name: name);
    return adopted;
  }
}
