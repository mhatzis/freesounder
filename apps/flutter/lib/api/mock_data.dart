import '../models/music_service.dart';
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
    configuredServices = [
      const MusicService(
        instanceId: 'spotify-demo',
        domain: 'spotify',
        name: 'Spotify',
        status: 'loaded',
        available: true,
        account: 'demo@example.com',
        authKind: 'oauth',
        canReauthenticate: true,
        values: {'username': 'demo@example.com'},
      ),
    ];
    serviceTypes = const [
      MusicServiceType(domain: 'spotify', name: 'Spotify', authKind: 'oauth', availableOnServer: true, configured: true),
      MusicServiceType(domain: 'tidal', name: 'Tidal', authKind: 'oauth', availableOnServer: true),
      MusicServiceType(
        domain: 'apple_music',
        name: 'Apple Music',
        authKind: 'oauth',
        availableOnServer: true,
        limited: true,
        limitation: appleMusicLimitation,
      ),
      MusicServiceType(domain: 'deezer', name: 'Deezer', authKind: 'oauth', availableOnServer: true),
      MusicServiceType(domain: 'ytmusic', name: 'YouTube Music', availableOnServer: true),
      MusicServiceType(domain: 'qobuz', name: 'Qobuz', availableOnServer: true),
      MusicServiceType(domain: 'soundcloud', name: 'SoundCloud', availableOnServer: true),
      MusicServiceType(domain: 'tunein', name: 'TuneIn', category: 'radio', authKind: 'none', availableOnServer: true),
      MusicServiceType(domain: 'radiobrowser', name: 'Radio Browser', category: 'radio', authKind: 'none', availableOnServer: true),
      MusicServiceType(domain: 'plex', name: 'Plex', category: 'media_server', availableOnServer: true),
      MusicServiceType(domain: 'jellyfin', name: 'Jellyfin', category: 'media_server', availableOnServer: true),
      MusicServiceType(domain: 'opensubsonic', name: 'OpenSubsonic', category: 'media_server', availableOnServer: true),
      MusicServiceType(domain: 'filesystem_local', name: 'Local files', category: 'files', authKind: 'path', availableOnServer: true, multiInstance: true),
      MusicServiceType(domain: 'filesystem_smb', name: 'SMB share', category: 'files', availableOnServer: true, multiInstance: true),
      MusicServiceType(domain: 'filesystem_nfs', name: 'NFS share', category: 'files', authKind: 'path', availableOnServer: false, multiInstance: true),
    ];
    spotifyConnect = const SpotifyConnectStatus(
      available: true,
      configured: true,
      engine: 'go_librespot',
      engineLabel: 'go-librespot',
      status: 'loaded',
      rooms: [
        SpotifyConnectRoom(playerId: 'kitchen', name: 'Kitchen', enabled: true),
        SpotifyConnectRoom(playerId: 'lounge', name: 'Lounge', enabled: false),
        SpotifyConnectRoom(playerId: 'bedroom', name: 'Bedroom', enabled: false),
      ],
      note: 'Demo household: Spotify Connect status is simulated.',
    );
  }

  late List<Room> rooms;
  late Map<String, NowPlaying> nowPlaying;
  late Map<String, PlayerSettings> settings;
  late List<UnclaimedPlayer> unclaimed;
  late List<MusicService> configuredServices;
  late List<MusicServiceType> serviceTypes;
  late SpotifyConnectStatus spotifyConnect;
  String activeRoomId = 'kitchen';
  var _serviceSeq = 0;

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
        url: 'http://$ip:8090',
        address: ip,
        port: 8090,
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

  MusicService addService(String domain, {Map<String, dynamic> values = const {}, String? name}) {
    final type = serviceTypes.firstWhere(
      (item) => item.domain == domain,
      orElse: () => MusicServiceType(domain: domain, name: domain),
    );
    if (domain == 'filesystem_nfs') {
      throw const FormatException(
        'NFS shares are not available in this Music Assistant version. Use an SMB share or a local folder on the host.',
      );
    }
    if (!type.multiInstance && configuredServices.any((item) => item.domain == domain)) {
      throw const FormatException('This service is already connected.');
    }
    _serviceSeq += 1;
    final service = MusicService(
      instanceId: '$domain-$_serviceSeq',
      domain: domain,
      name: name ?? type.name,
      status: 'loaded',
      available: true,
      account: values['username'] as String?,
      limited: type.limited,
      limitation: type.limitation,
      authKind: type.authKind,
      canReauthenticate: type.authKind == 'oauth',
      values: Map<String, dynamic>.from(values)..removeWhere((key, _) => key.contains('pass') || key.contains('token')),
    );
    configuredServices = [...configuredServices, service];
    return service;
  }

  void removeService(String instanceId) {
    configuredServices = [for (final item in configuredServices) if (item.instanceId != instanceId) item];
  }

  void setSpotifyConnect(String playerId, bool enabled) {
    spotifyConnect = SpotifyConnectStatus(
      available: spotifyConnect.available,
      configured: spotifyConnect.configured,
      instanceId: spotifyConnect.instanceId,
      engine: spotifyConnect.engine,
      engineLabel: spotifyConnect.engineLabel,
      status: spotifyConnect.status,
      note: spotifyConnect.note,
      rooms: [
        for (final room in spotifyConnect.rooms)
          SpotifyConnectRoom(
            playerId: room.playerId,
            name: room.name,
            enabled: room.playerId == playerId ? enabled : room.enabled,
          ),
      ],
    );
  }
}
