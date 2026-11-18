class NowPlaying {
  const NowPlaying({
    required this.playerId,
    this.title,
    this.artist,
    this.album,
    this.imageUrl,
    this.state = 'idle',
    this.position,
    this.duration,
    this.source,
    this.groupName,
    this.groupId,
    this.stationName,
    this.stationImageUrl,
    this.contextName,
    this.uri,
    this.receivedAt,
    this.metaUpdatedAt,
    this.metaFrom,
    this.titleCheckedAt,
    this.titleError,
    this.titleSource,
  });

  final String playerId;
  final String? title;
  final String? artist;
  final String? album;
  final String? imageUrl;
  final String state;

  /// Seconds, sampled at [receivedAt].
  final double? position;
  final double? duration;

  /// none, radio, spotify or airplay.
  final String? source;
  final String? groupName;
  final String? groupId;
  final String? stationName;
  final String? stationImageUrl;
  final String? contextName;
  final String? uri;
  final DateTime? receivedAt;

  /// When the leader last saw the song change (developer line).
  final DateTime? metaUpdatedAt;

  /// Room whose player produced this metadata (the group leader).
  final String? metaFrom;

  /// Radio: when the leader last read the stream's song title, and why not.
  final DateTime? titleCheckedAt;
  final String? titleError;

  /// "stream": titles read from the playing stream's ICY metadata; "poll":
  /// from a separate connection (fallback).
  final String? titleSource;

  bool get isPlaying => state == 'playing';
  bool get isPaused => state == 'paused';
  bool get hasTrack => title != null && title!.isNotEmpty;

  /// Identity of the song on screen. Art widgets are keyed by it, so a new
  /// song always gets a fresh image even when the art URL did not change.
  String get trackKey => [source, uri, title, artist, album, imageUrl].map((v) => v ?? '').join('\u0000');

  /// Position now, moving on while playing (clamped to the duration).
  double? livePosition([DateTime? now]) {
    final base = position;
    if (base == null) {
      return null;
    }
    var value = base;
    final at = receivedAt;
    if (isPlaying && at != null) {
      value += (now ?? DateTime.now()).difference(at).inMilliseconds / 1000;
    }
    final total = duration;
    if (total != null && total > 0 && value > total) {
      value = total;
    }
    return value < 0 ? 0 : value;
  }

  /// Artwork to show, best first: track art, then station logo.
  List<String> get artworkCandidates => [
        for (final url in [imageUrl, stationImageUrl])
          if (url != null && url.startsWith('http')) url,
      ];

  factory NowPlaying.fromJson(Map<String, dynamic> json, {DateTime? receivedAt}) {
    return NowPlaying(
      playerId: json['player_id'] as String,
      title: json['title'] as String?,
      artist: json['artist'] as String?,
      album: json['album'] as String?,
      imageUrl: json['image_url'] as String?,
      state: json['state'] as String? ?? 'idle',
      position: (json['position'] as num?)?.toDouble(),
      duration: (json['duration'] as num?)?.toDouble(),
      source: json['source'] as String?,
      groupName: json['group_name'] as String?,
      groupId: json['group_id'] as String?,
      stationName: json['station_name'] as String?,
      stationImageUrl: json['station_image_url'] as String?,
      contextName: json['context_name'] as String?,
      uri: json['uri'] as String?,
      receivedAt: receivedAt ?? DateTime.now(),
      metaUpdatedAt: _epoch(json['meta_updated_at']),
      metaFrom: json['meta_from'] as String?,
      titleCheckedAt: _epoch(json['title_checked_at']),
      titleError: json['title_error'] as String?,
      titleSource: json['title_source'] as String?,
    );
  }

  NowPlaying copyWith({String? state, String? title, double? position, DateTime? receivedAt}) {
    return NowPlaying(
      playerId: playerId,
      title: title ?? this.title,
      artist: artist,
      album: album,
      imageUrl: imageUrl,
      state: state ?? this.state,
      position: position ?? this.position,
      duration: duration,
      source: source,
      groupName: groupName,
      groupId: groupId,
      stationName: stationName,
      stationImageUrl: stationImageUrl,
      contextName: contextName,
      uri: uri,
      receivedAt: receivedAt ?? this.receivedAt,
      metaUpdatedAt: metaUpdatedAt,
      metaFrom: metaFrom,
      titleCheckedAt: titleCheckedAt,
      titleError: titleError,
      titleSource: titleSource,
    );
  }
}

DateTime? _epoch(Object? v) =>
    v is num ? DateTime.fromMillisecondsSinceEpoch((v * 1000).round()) : null;

String _ago(DateTime t, DateTime now) {
  final s = now.difference(t).inSeconds;
  if (s < 0) return 'just now';
  if (s < 90) return '${s}s ago';
  if (s < 5400) return '${(s / 60).round()} min ago';
  return '${(s / 3600).toStringAsFixed(1)} h ago';
}

String _clock(DateTime t) {
  final l = t.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(l.hour)}:${two(l.minute)}:${two(l.second)}';
}

/// Developer-mode line: how fresh this now-playing information is.
String metadataDebugLine(NowPlaying np, [DateTime? now]) {
  final at = now ?? DateTime.now();
  return [
    if (np.metaUpdatedAt != null) 'song changed ${_clock(np.metaUpdatedAt!)}',
    if (np.titleCheckedAt != null)
      '${np.titleSource == 'poll' ? 'title poll' : 'stream title'} ${_ago(np.titleCheckedAt!, at)}'
    else if (np.titleSource != null)
      '${np.titleSource == 'poll' ? 'title poll' : 'stream title'}: none yet',
    if (np.titleError != null && np.titleError!.trim().isNotEmpty) 'error ${np.titleError}',
    if (np.metaFrom != null) 'by ${np.metaFrom}',
    if (np.receivedAt != null) 'app got it ${_ago(np.receivedAt!, at)}',
  ].join(' · ');
}
