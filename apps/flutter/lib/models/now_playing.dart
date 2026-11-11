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
  });

  final String playerId;
  final String? title;
  final String? artist;
  final String? album;
  final String? imageUrl;
  final String state;
  final double? position;
  final double? duration;

  /// none, radio, spotify or airplay.
  final String? source;
  final String? groupName;

  bool get isPlaying => state == 'playing';
  bool get isPaused => state == 'paused';

  factory NowPlaying.fromJson(Map<String, dynamic> json) {
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
    );
  }

  NowPlaying copyWith({String? state, String? title}) {
    return NowPlaying(
      playerId: playerId,
      title: title ?? this.title,
      artist: artist,
      album: album,
      imageUrl: imageUrl,
      state: state ?? this.state,
      position: position,
      duration: duration,
      source: source,
      groupName: groupName,
    );
  }
}
