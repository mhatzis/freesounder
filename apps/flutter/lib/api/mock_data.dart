import '../models/now_playing.dart';
import '../models/player_settings.dart';
import '../models/radio_station.dart';
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
        url: 'http://192.168.1.42:8080',
        address: '192.168.1.42',
        port: 8080,
        role: 'room',
      ),
    ];
  }

  late List<Room> rooms;
  late Map<String, NowPlaying> nowPlaying;
  late Map<String, PlayerSettings> settings;
  late List<UnclaimedPlayer> unclaimed;
  String activeRoomId = 'kitchen';

  void offerProvisioned({required String id, required String name, String? address}) {
    if (unclaimed.any((player) => player.id == id) || rooms.any((room) => room.id == id)) {
      return;
    }
    final ip = address ?? '192.168.1.80';
    unclaimed = [
      ...unclaimed,
      UnclaimedPlayer(
        id: id,
        name: name,
        url: 'http://$ip:8080',
        address: ip,
        port: 8080,
      ),
    ];
  }

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

  final List<RadioStation> radioStations = const [
    RadioStation(name: 'Demo FM', stationuuid: 'demo-fm', countrycode: 'AU', bitrate: 128, codec: 'MP3'),
    RadioStation(name: 'Example Jazz', stationuuid: 'demo-jazz', tags: 'jazz', bitrate: 96, codec: 'AAC'),
  ];

  void setGroup(String leaderId, List<String> memberIds) {
    final members = [leaderId, ...memberIds.where((id) => id != leaderId)];
    final leader = rooms.firstWhere((r) => r.id == leaderId);
    final name = members.length > 1 ? '${leader.name} + ${members.length - 1}' : leader.name;
    rooms = [
      for (final room in rooms)
        if (members.contains(room.id))
          Room(
            id: room.id,
            name: room.name,
            volume: room.volume,
            state: room.state,
            groupId: leaderId,
            memberIds: members.length > 1 ? members : const [],
            groupName: name,
            leaderId: leaderId,
          )
        else if (room.groupId == leaderId)
          Room(id: room.id, name: room.name, volume: room.volume, state: room.state, groupId: room.id)
        else
          room,
    ];
  }

  void ungroup(String roomId) {
    final room = rooms.firstWhere((r) => r.id == roomId);
    final leader = room.groupId ?? roomId;
    final remaining = room.memberIds.where((id) => id != roomId).toList();
    rooms = [
      for (final r in rooms)
        if (r.id == roomId)
          Room(id: r.id, name: r.name, volume: r.volume, state: 'idle', groupId: r.id)
        else
          r,
    ];
    if (remaining.isNotEmpty) {
      setGroup(remaining.contains(leader) ? leader : remaining.first, remaining);
    }
  }
}
