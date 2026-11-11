/// Bluetooth setup steps, friendly errors, and radio readiness.
///
/// Pure Dart (no plugin imports) so it can be unit tested without a radio.
library;

import 'dart:async';

/// The Improv setup steps, in order. Shown in the developer detail line.
enum BleStep {
  radio('Bluetooth'),
  scan('Scanning'),
  connect('Connecting'),
  discover('Finding the setup service'),
  notify('Subscribing to updates'),
  deviceInfo('Reading player details'),
  scanWifi('Listing Wi-Fi networks'),
  sendWifi('Sending Wi-Fi details'),
  disconnect('Disconnecting');

  const BleStep(this.label);
  final String label;
}

/// Mirrors flutter_blue_plus BluetoothAdapterState without importing it.
enum RadioState { unknown, unavailable, unauthorized, turningOn, on, turningOff, off }

/// A setup failure with a message safe to show anyone, plus raw detail for
/// developer mode.
class NearbyException implements Exception {
  const NearbyException(this.step, this.message, {this.detail = '', this.settingsHint = false});

  final BleStep step;
  final String message;
  final String detail;

  /// True when the fix is in the iOS/Android Settings app.
  final bool settingsHint;

  String get debugLine => detail.isEmpty ? step.label : '${step.label}: $detail';

  @override
  String toString() => message;
}

/// Wait until the radio reports [RadioState.on]. iOS reports `unknown`
/// for a moment after launch; scanning then fails with
/// "bluetooth must be turned on (CBManagerStateUnknown)".
Future<void> awaitRadioOn(
  Stream<RadioState> states, {
  Duration timeout = const Duration(seconds: 6),
}) async {
  RadioState last = RadioState.unknown;
  try {
    await states
        .map((state) {
          last = state;
          if (state == RadioState.unauthorized) {
            throw const NearbyException(
              BleStep.radio,
              'FreeSounder needs Bluetooth permission to find new players. '
              'Turn it on in Settings > FreeSounder > Bluetooth.',
              detail: 'adapter state unauthorized',
              settingsHint: true,
            );
          }
          if (state == RadioState.unavailable) {
            throw const NearbyException(
              BleStep.radio,
              'This device does not support Bluetooth Low Energy.',
              detail: 'adapter state unavailable',
            );
          }
          return state;
        })
        .firstWhere((state) => state == RadioState.on)
        .timeout(timeout);
  } on TimeoutException {
    if (last == RadioState.off || last == RadioState.turningOff) {
      throw const NearbyException(
        BleStep.radio,
        'Bluetooth is off. Turn it on in Settings or Control Center, then search again.',
        detail: 'adapter state off',
        settingsHint: true,
      );
    }
    throw NearbyException(
      BleStep.radio,
      'Bluetooth is not ready yet. Wait a moment and search again.',
      detail: 'adapter state ${last.name} after ${timeout.inSeconds}s',
    );
  } on StateError {
    throw NearbyException(
      BleStep.radio,
      'Bluetooth is not ready yet. Wait a moment and search again.',
      detail: 'adapter state stream ended in ${last.name}',
    );
  }
}

/// Run [action] for [step] with a timeout, turning any failure into a
/// [NearbyException] with a friendly message.
Future<T> runStep<T>(
  BleStep step,
  Future<T> Function() action, {
  required Duration timeout,
  void Function(BleStep step, String? error)? onStep,
}) async {
  onStep?.call(step, null);
  try {
    return await action().timeout(timeout);
  } on NearbyException catch (err) {
    onStep?.call(step, err.detail);
    rethrow;
  } on TimeoutException {
    final detail = 'timed out after ${timeout.inSeconds}s';
    onStep?.call(step, detail);
    throw NearbyException(step, friendlyStepMessage(step, timedOut: true), detail: detail);
  } on Object catch (err) {
    final detail = err.toString();
    onStep?.call(step, detail);
    throw NearbyException(step, friendlyStepMessage(step), detail: detail);
  }
}

String friendlyStepMessage(BleStep step, {bool timedOut = false}) {
  final suffix = timedOut ? ' It did not answer in time.' : '';
  switch (step) {
    case BleStep.radio:
      return 'Bluetooth is not ready yet. Wait a moment and search again.';
    case BleStep.scan:
      return 'Could not search for nearby players.$suffix Search again.';
    case BleStep.connect:
      return 'Could not connect to the player.$suffix Move closer and tap it to try again.';
    case BleStep.discover:
    case BleStep.notify:
      return 'Connected, but the player did not finish setting up Bluetooth.$suffix '
          'Tap it to try again. If it keeps happening, restart the player.';
    case BleStep.deviceInfo:
    case BleStep.scanWifi:
      return 'The player stopped answering.$suffix Tap it to try again.';
    case BleStep.sendWifi:
      return 'The player did not confirm the Wi-Fi details.$suffix Try again.';
    case BleStep.disconnect:
      return 'Disconnected from the player.';
  }
}

/// Text safe to show in the UI for any error. Raw exceptions never reach
/// the screen; developer mode shows [debugDetail] separately.
String friendlyError(Object err, {String fallback = 'Something went wrong. Try again.'}) {
  if (err is NearbyException) {
    return err.message;
  }
  if (err is TimeoutException) {
    return 'That took too long. Try again.';
  }
  return fallback;
}

String debugDetail(Object err) {
  if (err is NearbyException) {
    return err.debugLine;
  }
  return err.toString();
}

/// Show "FreeSounder-44BD" for the short scan-response name "FS-44BD".
String displayNameForAdvert(String name) {
  if (name.startsWith('FS-') && name.length > 3) {
    return 'FreeSounder-${name.substring(3)}';
  }
  if (name.isEmpty || name == 'FS') {
    return 'FreeSounder';
  }
  return name;
}
