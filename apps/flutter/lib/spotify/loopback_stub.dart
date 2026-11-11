/// No loopback listener on this platform (web): the custom scheme is used.
class LoopbackBounce {
  static Future<LoopbackBounce?> start(int port, String target) async => null;

  Future<void> close() async {}
}
