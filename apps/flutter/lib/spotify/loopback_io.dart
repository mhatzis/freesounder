import 'dart:io';

/// Listens on http://127.0.0.1:PORT/callback while the Spotify sign-in sheet
/// is open and bounces Spotify's redirect into [target] (freesounder://…), so
/// the sign-in sheet sees the custom scheme and closes. For Spotify apps that
/// refuse custom-scheme redirect URIs.
class LoopbackBounce {
  LoopbackBounce._(this._server);

  final HttpServer _server;

  static Future<LoopbackBounce?> start(int port, String target) async {
    final HttpServer server;
    try {
      server = await HttpServer.bind(InternetAddress.loopbackIPv4, port, shared: true);
    } on SocketException {
      return null;
    }
    server.listen((request) async {
      final query = request.uri.query;
      final location = query.isEmpty ? target : '$target?$query';
      request.response
        ..statusCode = HttpStatus.found
        ..headers.set(HttpHeaders.locationHeader, location)
        ..headers.contentType = ContentType.html
        ..write('<html><body><a href="$location">Back to FreeSounder</a></body></html>');
      await request.response.close();
    });
    return LoopbackBounce._(server);
  }

  Future<void> close() => _server.close(force: true);
}
