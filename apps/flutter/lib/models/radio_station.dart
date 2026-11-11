/// A Radio Browser station (https://www.radio-browser.info), as returned by a
/// player's `/api/v2/radio/search`.
class RadioStation {
  const RadioStation({
    required this.name,
    this.stationuuid,
    this.url,
    this.favicon,
    this.country,
    this.countrycode,
    this.tags,
    this.codec,
    this.bitrate,
  });

  final String? stationuuid;
  final String name;
  final String? url;
  final String? favicon;
  final String? country;
  final String? countrycode;
  final String? tags;
  final String? codec;
  final int? bitrate;

  factory RadioStation.fromJson(Map<String, dynamic> json) {
    String? text(String key) {
      final value = json[key];
      if (value is String && value.trim().isNotEmpty) {
        return value.trim();
      }
      return null;
    }

    return RadioStation(
      stationuuid: text('stationuuid'),
      name: text('name') ?? 'Unnamed station',
      url: text('url_resolved') ?? text('url'),
      favicon: text('favicon'),
      country: text('country'),
      countrycode: text('countrycode'),
      tags: text('tags'),
      codec: text('codec'),
      bitrate: (json['bitrate'] as num?)?.toInt(),
    );
  }

  Map<String, dynamic> toPlayJson() => {
        'stationuuid': ?stationuuid,
        'url': ?url,
        'name': name,
        'favicon': ?favicon,
      };

  /// "AU · 128 kbps MP3" style subtitle.
  String get subtitle {
    final parts = <String>[
      if (countrycode != null) countrycode!,
      if (bitrate != null && bitrate! > 0) '$bitrate kbps${codec == null ? '' : ' $codec'}',
      if (tags != null) tags!.split(',').take(3).join(', '),
    ];
    return parts.join(' · ');
  }
}
