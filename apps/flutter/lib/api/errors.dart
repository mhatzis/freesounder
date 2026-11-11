import 'dart:async';

import 'control_client.dart';

/// Turn a control-API failure into something a person can act on.
String userMessageFor(Object err) {
  if (err is ControlApiException) {
    if (err.systemStarting) {
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
  final parts = [
    'Your system is still starting…',
    if (detail != null && detail.isNotEmpty) detail,
  ];
  return parts.join(' ');
}
