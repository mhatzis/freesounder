/// Podcasts (iTunes directory search + RSS feeds read by the players).
library;

String? _text(Object? value) => value is String && value.trim().isNotEmpty ? value.trim() : null;

class Podcast {
  const Podcast({required this.feed, required this.title, this.author, this.image, this.genre});

  final String feed;
  final String title;
  final String? author;
  final String? image;
  final String? genre;

  factory Podcast.fromJson(Map<String, dynamic> json) => Podcast(
    feed: json['feed'] as String,
    title: _text(json['title']) ?? 'Podcast',
    author: _text(json['author']),
    image: _text(json['image']),
    genre: _text(json['genre']),
  );

  Map<String, dynamic> toJson() => {'feed': feed, 'title': title, 'author': ?author, 'image': ?image};
}

class PodcastEpisode {
  const PodcastEpisode({
    required this.id,
    required this.title,
    required this.url,
    this.date,
    this.duration,
    this.image,
    this.description,
    this.position,
    this.done = false,
  });

  final String id;
  final String title;
  final String url;
  final DateTime? date;
  final double? duration;
  final String? image;
  final String? description;

  /// Where listening stopped (seconds), shared by every phone in the home.
  final double? position;
  final bool done;

  /// 0..1 when partly listened, else null.
  double? get progress {
    final d = duration, p = position;
    if (done) {
      return 1;
    }
    if (d == null || d <= 0 || p == null || p <= 10) {
      return null;
    }
    return (p / d).clamp(0.0, 1.0);
  }

  bool get canResume => !done && (position ?? 0) > 10;

  factory PodcastEpisode.fromJson(Map<String, dynamic> json) {
    final ts = json['date'] as num?;
    return PodcastEpisode(
      id: json['id'] as String,
      title: _text(json['title']) ?? 'Episode',
      url: json['url'] as String,
      date: ts == null ? null : DateTime.fromMillisecondsSinceEpoch((ts * 1000).round()),
      duration: (json['duration'] as num?)?.toDouble(),
      image: _text(json['image']),
      description: _text(json['description']),
      position: (json['position'] as num?)?.toDouble(),
      done: json['done'] == true,
    );
  }

  Map<String, dynamic> toJson() => {'id': id, 'url': url, 'title': title, 'image': ?image, 'duration': ?duration};
}

class PodcastFeed {
  const PodcastFeed({required this.podcast, this.description, this.episodes = const [], this.subscribed = false});

  final Podcast podcast;
  final String? description;
  final List<PodcastEpisode> episodes;
  final bool subscribed;

  factory PodcastFeed.fromJson(Map<String, dynamic> json) => PodcastFeed(
    podcast: Podcast.fromJson(json),
    description: _text(json['description']),
    episodes: [
      for (final e in (json['episodes'] as List? ?? const [])) PodcastEpisode.fromJson(e as Map<String, dynamic>),
    ],
    subscribed: json['subscribed'] == true,
  );
}

/// "1 h 05 min", "42 min", "50 s".
String formatEpisodeLength(double? seconds) {
  if (seconds == null || seconds <= 0) {
    return '';
  }
  final s = seconds.round();
  if (s < 60) {
    return '$s s';
  }
  final h = s ~/ 3600, m = (s % 3600) ~/ 60;
  return h > 0 ? '$h h ${m.toString().padLeft(2, '0')} min' : '$m min';
}
