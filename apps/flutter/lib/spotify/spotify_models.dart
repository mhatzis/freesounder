/// One thing from Spotify the app can show and play.
class SpotifyItem {
  const SpotifyItem({
    required this.type,
    required this.id,
    required this.uri,
    required this.name,
    this.subtitle,
    this.imageUrl,
    this.durationMs,
    this.contextUri,
  });

  /// track, album, artist, playlist, show or episode.
  final String type;
  final String id;
  final String uri;
  final String name;
  final String? subtitle;
  final String? imageUrl;
  final int? durationMs;

  /// For a track inside an album or playlist page: the context to play it in.
  final String? contextUri;

  bool get isPlayableContext => type == 'album' || type == 'playlist' || type == 'artist' || type == 'show';

  SpotifyItem inContext(String? context) => SpotifyItem(
        type: type,
        id: id,
        uri: uri,
        name: name,
        subtitle: subtitle,
        imageUrl: imageUrl,
        durationMs: durationMs,
        contextUri: context,
      );

  static String? bestImage(Object? images, {bool small = false}) {
    if (images is! List || images.isEmpty) {
      return null;
    }
    final list = [for (final i in images) if (i is Map && i['url'] is String) i];
    if (list.isEmpty) {
      return null;
    }
    list.sort((a, b) => ((a['width'] as num?) ?? 0).compareTo((b['width'] as num?) ?? 0));
    if (small) {
      return (list.firstWhere((i) => ((i['width'] as num?) ?? 0) >= 160, orElse: () => list.last))['url'] as String;
    }
    return list.last['url'] as String;
  }

  static String _artists(Object? artists) =>
      artists is List ? [for (final a in artists) if (a is Map) a['name']].whereType<String>().join(', ') : '';

  /// Parse any Spotify Web API object we use; null for unknown or local items.
  static SpotifyItem? fromJson(Object? json, {Object? fallbackImages}) {
    if (json is! Map) {
      return null;
    }
    final type = json['type'] as String?;
    final id = json['id'] as String?;
    final uri = json['uri'] as String?;
    final name = json['name'] as String?;
    if (type == null || id == null || uri == null || name == null || json['is_local'] == true) {
      return null;
    }
    String? subtitle;
    Object? images = json['images'];
    switch (type) {
      case 'track':
        final album = json['album'];
        subtitle = _artists(json['artists']);
        images = album is Map ? album['images'] : fallbackImages;
      case 'album':
        subtitle = [_artists(json['artists']), (json['release_date'] as String?)?.split('-').first]
            .whereType<String>()
            .where((s) => s.isNotEmpty)
            .join(' · ');
      case 'playlist':
        final owner = json['owner'];
        subtitle = owner is Map ? 'By ${owner['display_name'] ?? owner['id'] ?? 'Spotify'}' : null;
      case 'artist':
        subtitle = 'Artist';
      case 'show':
        subtitle = json['publisher'] as String? ?? 'Podcast';
      case 'episode':
        subtitle = (json['release_date'] as String?) ?? 'Episode';
        images ??= fallbackImages;
      default:
        return null;
    }
    return SpotifyItem(
      type: type,
      id: id,
      uri: uri,
      name: name,
      subtitle: (subtitle == null || subtitle.isEmpty) ? null : subtitle,
      imageUrl: bestImage(images ?? fallbackImages, small: true),
      durationMs: (json['duration_ms'] as num?)?.toInt(),
    );
  }
}

class SpotifySection {
  const SpotifySection(this.title, this.items);

  final String title;
  final List<SpotifyItem> items;
}

/// A page: album, playlist, artist or show, with its tracks.
class SpotifyPage {
  const SpotifyPage({required this.item, this.imageUrl, this.tracks = const [], this.sections = const [], this.note});

  final SpotifyItem item;
  final String? imageUrl;
  final List<SpotifyItem> tracks;
  final List<SpotifySection> sections;

  /// e.g. why a playlist's tracks are not listed.
  final String? note;
}
