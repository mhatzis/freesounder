/// Music library (NAS / USB) types, as returned by a player's
/// `/api/v2/library/*` endpoints. The library lives on one player of the home
/// (the one with the shares mounted); any room can play from it.
library;

String? _text(Object? value) => value is String && value.trim().isNotEmpty ? value.trim() : null;

class LibraryTrack {
  const LibraryTrack({required this.uri, required this.title, this.artist, this.album, this.duration});

  final String uri;
  final String title;
  final String? artist;
  final String? album;
  final double? duration;

  factory LibraryTrack.fromJson(Map<String, dynamic> json) => LibraryTrack(
    uri: json['uri'] as String,
    title: _text(json['title']) ?? (json['uri'] as String).split('/').last,
    artist: _text(json['artist']),
    album: _text(json['album']),
    duration: (json['duration'] as num?)?.toDouble(),
  );

  Map<String, dynamic> toJson() => {
    'uri': uri,
    'title': title,
    'artist': ?artist,
    'album': ?album,
    'duration': ?duration,
  };
}

class LibraryAlbum {
  const LibraryAlbum({required this.album, this.artist});

  final String album;
  final String? artist;

  factory LibraryAlbum.fromJson(Map<String, dynamic> json) =>
      LibraryAlbum(album: json['album'] as String, artist: _text(json['artist']));
}

class LibraryFolder {
  const LibraryFolder({required this.path, required this.name});

  final String path;
  final String name;

  factory LibraryFolder.fromJson(Map<String, dynamic> json) =>
      LibraryFolder(path: json['path'] as String, name: _text(json['name']) ?? json['path'] as String);
}

class LibraryListing {
  const LibraryListing({this.path = '', this.folders = const [], this.tracks = const []});

  final String path;
  final List<LibraryFolder> folders;
  final List<LibraryTrack> tracks;

  factory LibraryListing.fromJson(Map<String, dynamic> json) => LibraryListing(
    path: json['path'] as String? ?? '',
    folders: [
      for (final f in (json['folders'] as List? ?? const [])) LibraryFolder.fromJson(f as Map<String, dynamic>),
    ],
    tracks: [for (final t in (json['tracks'] as List? ?? const [])) LibraryTrack.fromJson(t as Map<String, dynamic>)],
  );
}

class LibrarySearchResult {
  const LibrarySearchResult({this.artists = const [], this.albums = const [], this.tracks = const []});

  final List<String> artists;
  final List<LibraryAlbum> albums;
  final List<LibraryTrack> tracks;

  bool get isEmpty => artists.isEmpty && albums.isEmpty && tracks.isEmpty;

  factory LibrarySearchResult.fromJson(Map<String, dynamic> json) => LibrarySearchResult(
    artists: [for (final a in (json['artists'] as List? ?? const [])) a as String],
    albums: [for (final a in (json['albums'] as List? ?? const [])) LibraryAlbum.fromJson(a as Map<String, dynamic>)],
    tracks: [for (final t in (json['tracks'] as List? ?? const [])) LibraryTrack.fromJson(t as Map<String, dynamic>)],
  );
}

/// A network share or USB drive. The password is never sent back: only
/// whether one is stored.
class LibraryShare {
  const LibraryShare({
    required this.id,
    required this.kind,
    required this.name,
    this.host,
    this.share,
    this.username,
    this.hasPassword = false,
    this.mounted = false,
    this.error,
  });

  final String id;
  final String kind; // smb, nfs, usb
  final String name;
  final String? host;
  final String? share;
  final String? username;
  final bool hasPassword;
  final bool mounted;
  final String? error;

  String get address => switch (kind) {
    'smb' => '\\\\${host ?? ''}\\${share ?? ''}',
    'nfs' => '${host ?? ''}:${share ?? ''}',
    _ => 'USB drive',
  };

  factory LibraryShare.fromJson(Map<String, dynamic> json) => LibraryShare(
    id: json['id'] as String,
    kind: json['kind'] as String? ?? 'smb',
    name: _text(json['name']) ?? _text(json['share']) ?? json['id'] as String,
    host: _text(json['host']),
    share: _text(json['share']),
    username: _text(json['username']),
    hasPassword: json['has_password'] == true,
    mounted: json['mounted'] == true,
    error: _text(json['error']),
  );
}

class LibrarySummary {
  const LibrarySummary({
    this.shares = const [],
    this.songs = 0,
    this.albums = 0,
    this.artists = 0,
    this.scanning = false,
    this.hostId,
    this.hostRoom,
  });

  final List<LibraryShare> shares;
  final int songs;
  final int albums;
  final int artists;
  final bool scanning;
  final String? hostId;
  final String? hostRoom;

  factory LibrarySummary.fromJson(Map<String, dynamic> json) {
    final stats = (json['stats'] as Map?)?.cast<String, dynamic>() ?? const {};
    int n(String key) => int.tryParse('${stats[key] ?? 0}') ?? 0;
    return LibrarySummary(
      shares: [for (final s in (json['shares'] as List? ?? const [])) LibraryShare.fromJson(s as Map<String, dynamic>)],
      songs: n('songs'),
      albums: n('albums'),
      artists: n('artists'),
      scanning: json['scanning'] == true,
      hostId: _text(json['host_id']),
      hostRoom: _text(json['host_room']),
    );
  }
}

class UsbDrive {
  const UsbDrive({required this.uuid, required this.name, this.size});

  final String uuid;
  final String name;
  final String? size;

  factory UsbDrive.fromJson(Map<String, dynamic> json) =>
      UsbDrive(uuid: json['uuid'] as String, name: _text(json['name']) ?? 'USB drive', size: _text(json['size']));
}
