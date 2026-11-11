import 'dart:async';

import 'package:flutter_web_auth_2/flutter_web_auth_2.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/control_client.dart';
import '../models/music_service.dart';

/// Open an OAuth or external login URL and wait until the control API reports completion.
///
/// The control API starts a Music Assistant setup flow and returns [AuthSession.authUrl].
/// The app opens that URL in an ASWebAuthenticationSession-style sheet when possible.
/// Completion is reported over the control API WebSocket and confirmed by polling
/// `GET /music-services/auth/{id}`, because Music Assistant's own callback lands on
/// the household host, not always on `freesounder://`.
Future<AuthSession> completeExternalAuth({
  required ControlApiClient client,
  required AuthSession session,
  Stream<Map<String, dynamic>>? events,
}) async {
  var current = session;
  final pending = Completer<AuthSession>();

  StreamSubscription<Map<String, dynamic>>? sub;
  if (events != null) {
    sub = events.listen((message) {
      if (message['event'] != 'music_service_auth') {
        return;
      }
      final data = message['data'];
      if (data is Map<String, dynamic> && data['session_id'] == session.sessionId) {
        final next = AuthSession.fromJson(data);
        if (next.isComplete && !pending.isCompleted) {
          pending.complete(next);
        }
      }
    });
  }

  Future<void> poll() async {
    while (!pending.isCompleted) {
      await Future<void>.delayed(const Duration(seconds: 2));
      try {
        final next = await client.getMusicServiceAuth(session.sessionId);
        if (next.isComplete && !pending.isCompleted) {
          pending.complete(next);
        }
      } catch (_) {
        // Keep polling until the timeout below.
      }
    }
  }

  final pollTask = poll();
  final url = current.authUrl;
  if (url != null && url.isNotEmpty) {
    try {
      await FlutterWebAuth2.authenticate(
        url: url,
        callbackUrlScheme: current.callbackScheme,
      );
    } catch (_) {
      final parsed = Uri.tryParse(url);
      if (parsed != null) {
        await launchUrl(parsed, mode: LaunchMode.inAppBrowserView);
      }
    }
  }

  current = await pending.future.timeout(
    const Duration(minutes: 10),
    onTimeout: () => current,
  );
  await sub?.cancel();
  // Let the poll loop notice cancellation via the completer; ignore leftover errors.
  unawaited(pollTask);
  if (!current.isComplete) {
    current = await client.getMusicServiceAuth(session.sessionId);
  }
  return current;
}
