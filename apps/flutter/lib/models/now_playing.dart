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

  bool get isPlaying => state == 'playing';
  bool get isPaused => state == 'paused';
  bool get hasTrack => title != null && title!.isNotEmpty;

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
    );
  }
}
