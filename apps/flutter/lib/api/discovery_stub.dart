import '../models/player_settings.dart';

/// Web / non-IO stub — mDNS is not available.
Future<List<DiscoveredHost>> discoverControlApis({
  Duration timeout = const Duration(seconds: 3),
}) async {
  return const [];
}

Future<String?> discoverControlApi({
  Duration timeout = const Duration(seconds: 3),
}) async {
  return null;
}
