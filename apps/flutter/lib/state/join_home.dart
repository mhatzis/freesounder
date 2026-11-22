import 'package:flutter_riverpod/flutter_riverpod.dart';

/// A home on this Wi-Fi that this phone has not joined yet (0.6.19).
class JoinPrompt {
  const JoinPrompt({required this.householdId, required this.name});

  final String householdId;
  final String name;
}

class JoinHomeNotifier extends Notifier<JoinPrompt?> {
  @override
  JoinPrompt? build() => null;

  void show(JoinPrompt prompt) => state = prompt;

  void clear() => state = null;
}

/// Set while the app asks for the home's code; the app shell shows the join
/// screen instead of the rooms.
final joinHomeProvider = NotifierProvider<JoinHomeNotifier, JoinPrompt?>(JoinHomeNotifier.new);
