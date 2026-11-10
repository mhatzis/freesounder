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
  });

  final String id;
  final String name;
  final bool available;
  final int? volume;
  final bool? muted;
  final String? groupId;
  final bool isGroup;
  final List<String> memberIds;
  final String? state;

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
    );
  }
}
