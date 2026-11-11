import 'dart:async';

import 'control_client.dart';

/// Turn a control-API failure into something a person can act on.
String userMessageFor(Object err) {
  if (err is ControlApiException) {
    final starting = err.systemStarting ||
        (err.statusCode == 502 && (err.detail ?? err.message).toLowerCase().contains('music assistant'));
    if (starting) {
      return systemStartingMessage(err.maState, err.maDetail ?? err.detail);
    }
    return err.detail ?? err.message;
  }
  if (err is TimeoutException) {
    return 'Your system did not answer in time. Check the player is on and try again.';
  }
  return err.toString();
}

/// "Your system is still starting…" plus what it is doing, when known.
String systemStartingMessage(String? maState, String? detail) {
  final what = switch (maState) {
    'auth_required' => 'Music Assistant needs to be linked',
    'unavailable' => 'Music Assistant is not answering yet',
    _ => null,
  };
  final parts = [
    'Your system is still starting…',
    ?what,
    if (detail != null && detail.isNotEmpty) detail,
  ];
  return parts.join(' ');
}
