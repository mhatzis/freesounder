/// Web / non-IO stub — mDNS is not available, so discovery always returns null.
Future<String?> discoverControlApi({
  Duration timeout = const Duration(seconds: 3),
}) async {
  return null;
}
