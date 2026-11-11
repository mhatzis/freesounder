class Room {
  const Room({
    required this.id,
    required this.name,
    this.available = true,
    this.volume,
    this.muted,
    this.groupId,
    this.isGroup = false,
    this.memberIds = const [],
    this.state,
    this.groupName,
    this.leaderId,
    this.relayingFor,
    this.source,
    this.url,
    this.nowTitle,
    this.nowArtist,
    this.nowImageUrl,
  });

  /// Summary of what the room's group plays (from the rooms list).
  final String? nowTitle;
  final String? nowArtist;
  final String? nowImageUrl;

  final String id;
  final String name;
  final bool available;
  final int? volume;
  final bool? muted;
  final String? groupId;
  final bool isGroup;
  final List<String> memberIds;
  final String? state;

  /// "Kitchen + 1" for a grouped room, else the room name.
  final String? groupName;
  final String? leaderId;

  /// Set when this room left a playing group but still feeds it.
  final String? relayingFor;

  /// Active source of the room's group: none, radio, spotify or airplay.
  final String? source;
  final String? url;

  bool get isGrouped => memberIds.length > 1;

  factory Room.fromJson(Map<String, dynamic> json) {
    return Room(
      id: json['id'] as String,
      name: json['name'] as String? ?? json['id'] as String,
      available: json['available'] as bool? ?? true,
      volume: json['volume'] as int?,
      muted: json['muted'] as bool?,
      groupId: json['group_id'] as String?,
      isGroup: json['is_group'] as bool? ?? false,
      memberIds: [
        for (final id in json['member_ids'] as List? ?? const []) id.toString(),
      ],
      state: json['state'] as String?,
      groupName: json['group_name'] as String?,
      leaderId: json['leader_id'] as String?,
      relayingFor: json['relaying_for'] as String?,
      source: json['source'] as String?,
      url: json['url'] as String?,
      nowTitle: (json['now_playing'] as Map?)?['title'] as String?,
      nowArtist: (json['now_playing'] as Map?)?['artist'] as String?,
      nowImageUrl: (json['now_playing'] as Map?)?['image_url'] as String?,
    );
  }

  Room copyWith({
    String? name,
    int? volume,
    String? groupId,
    String? state,
    List<String>? memberIds,
    bool clearGroupId = false,
  }) {
    return Room(
      id: id,
      name: name ?? this.name,
      available: available,
      volume: volume ?? this.volume,
      muted: muted,
      groupId: clearGroupId ? null : (groupId ?? this.groupId),
      isGroup: isGroup,
      memberIds: memberIds ?? this.memberIds,
      state: state ?? this.state,
      groupName: groupName,
      leaderId: leaderId,
      relayingFor: relayingFor,
      source: source,
      url: url,
      nowTitle: nowTitle,
      nowArtist: nowArtist,
      nowImageUrl: nowImageUrl,
    );
  }
}
