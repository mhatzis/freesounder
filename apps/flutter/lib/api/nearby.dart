import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/player_settings.dart';
import '../state/playback_store.dart';
import 'nearby_factory.dart';

/// A FreeSounder player found over BLE (needs Wi-Fi) or already on the LAN.
class NearbyPlayer {
  const NearbyPlayer({
    required this.id,
    required this.name,
    required this.needsWifi,
    this.subtitle,
    this.unclaimed,
  });

  final String id;
  final String name;
  final bool needsWifi;
  final String? subtitle;
  final UnclaimedPlayer? unclaimed;
}

class WifiCandidate {
  const WifiCandidate({required this.ssid, required this.rssi, this.auth = 'NO'});

  final String ssid;
  final int rssi;
  final String auth;

  bool get isOpen => auth == 'NO' || auth.isEmpty;
}

class DeviceInfo {
  const DeviceInfo({
    this.firmwareName = 'FreeSounder',
    this.firmwareVersion = '',
    this.hardware = '',
    this.deviceName = '',
    this.playerId = '',
  });

  final String firmwareName;
  final String firmwareVersion;
  final String hardware;
  final String deviceName;
  final String playerId;
}

class ProvisionResult {
  const ProvisionResult({
    required this.ok,
    this.ip,
    this.redirect,
    this.reason,
    this.detail,
    this.playerId,
  });

  final bool ok;
  final String? ip;
  final String? redirect;
  final String? reason;

  /// Short, already-redacted explanation from the player (NetworkManager).
  final String? detail;
  final String? playerId;

  String get message {
    if (ok) {
      if (ip != null && ip!.isNotEmpty) {
        return 'Connected with address $ip';
      }
      return 'Connected';
    }
    switch (reason) {
      case 'wrong_password':
        return 'Wrong password. Check it and try again.';
      case 'invalid_password':
        return 'Wi-Fi passwords are 8 to 63 characters. Check it and try again.';
      case 'ssid_not_found':
        return 'The player cannot see that network. Move it closer to the router, '
            'or check the name (2.4 GHz and 5 GHz networks both work).';
      case 'radio_off':
        return "The player's Wi-Fi radio is switched off or blocked. Restart the player and try again.";
      case 'wifi_unmanaged':
        return "The player's Wi-Fi is not available for setup. Restart the player and try again.";
      case 'no_address':
        return 'The player joined the network but did not get an address. Check your router allows new devices.';
      case 'timeout':
        return 'The network did not answer in time. Move the player closer and try again.';
      default:
        return 'The player could not join the network. Check the password and try again.';
    }
  }
}

/// BLE (or demo) transport used by the add-player wizard.
abstract class NearbyTransport {
  Stream<List<NearbyPlayer>> get players;

  Future<void> startScan();

  Future<void> stopScan();

  Future<void> connect(NearbyPlayer player);

  Future<DeviceInfo> deviceInfo();

  Future<List<WifiCandidate>> scanWifi();

  /// Optional: ask the board to blink its LED. Never plays sound.
  Future<void> identify();

  /// Tell the player which Wi-Fi regulatory country it is in. Best effort.
  Future<bool> setWifiCountry(String country);

  Future<ProvisionResult> provision({
    required String ssid,
    required String password,
    String? roomName,
  });

  Future<void> disconnect();

  /// Best-effort current phone SSID, if the OS allows it.
  Future<String?> phoneSsid();

  /// Last Bluetooth step and error, for the developer-mode detail line.
  String get lastDebug;
}

/// Demo / test fake: one nearby unprovisioned player and two networks.
class DemoNearbyTransport implements NearbyTransport {
  DemoNearbyTransport({this.failReason, this.delay = const Duration(milliseconds: 400)});

  /// When set, [provision] reports this failure instead of success.
  final String? failReason;
  final Duration delay;

  static const demoPlayer = NearbyPlayer(
    id: 'demo-ble-porch',
    name: 'FreeSounder-A1B2',
    needsWifi: true,
    subtitle: 'Needs Wi-Fi (demo)',
  );

  final _controller = StreamController<List<NearbyPlayer>>.broadcast();
  var _scanning = false;

  @override
  Stream<List<NearbyPlayer>> get players => _controller.stream;

  @override
  Future<void> startScan() async {
    _scanning = true;
    await Future<void>.delayed(const Duration(milliseconds: 20));
    if (_scanning) {
      _controller.add(const [demoPlayer]);
    }
  }

  @override
  Future<void> stopScan() async {
    _scanning = false;
  }

  @override
  Future<void> connect(NearbyPlayer player) async {
    await Future<void>.delayed(delay);
  }

  @override
  Future<DeviceInfo> deviceInfo() async {
    return const DeviceInfo(
      firmwareName: 'FreeSounder',
      firmwareVersion: '0.3.0',
      hardware: 'demo',
      deviceName: 'FreeSounder-A1B2',
      playerId: 'demo-ble-porch',
    );
  }

  @override
  Future<List<WifiCandidate>> scanWifi() async {
    await Future<void>.delayed(delay);
    return const [
      WifiCandidate(ssid: 'Home', rssi: -40, auth: 'WPA2'),
      WifiCandidate(ssid: 'Cafe', rssi: -70, auth: 'NO'),
    ];
  }

  @override
  Future<void> identify() async {}

  @override
  Future<bool> setWifiCountry(String country) async => true;

  @override
  Future<ProvisionResult> provision({
    required String ssid,
    required String password,
    String? roomName,
  }) async {
    // Do not log [password].
    await Future<void>.delayed(delay);
    if (failReason != null) {
      return ProvisionResult(ok: false, reason: failReason, playerId: 'demo-ble-porch');
    }
    return ProvisionResult(
      ok: true,
      ip: '192.168.1.80',
      redirect: 'http://192.168.1.80:8080',
      playerId: 'demo-ble-porch',
    );
  }

  @override
  Future<void> disconnect() async {}

  @override
  Future<String?> phoneSsid() async => 'Home';

  @override
  String get lastDebug => 'Demo transport';
}

final nearbyTransportProvider = Provider<NearbyTransport>((ref) {
  final demo = ref.watch(playbackProvider.select((state) => state.mockMode));
  if (demo) {
    return DemoNearbyTransport();
  }
  return createPlatformNearbyTransport();
});

/// Wi-Fi list from an Improv SCAN_WIFI result: [ssid, rssi, auth]*.
List<WifiCandidate> parseWifiScan(List<String> strings) {
  final networks = <WifiCandidate>[];
  for (var i = 0; i + 2 < strings.length; i += 3) {
    final ssid = strings[i];
    if (ssid.isEmpty) {
      continue;
    }
    networks.add(
      WifiCandidate(ssid: ssid, rssi: int.tryParse(strings[i + 1]) ?? 0, auth: strings[i + 2]),
    );
  }
  return networks;
}

/// Decide the outcome from the WIFI_SETTINGS RPC result alone: a redirect
/// URL means joined; anything else is a failure reason.
ProvisionResult provisionResultFrom(List<String> strings, {String? playerId}) {
  final first = strings.isNotEmpty ? strings.first : '';
  if (first.startsWith('http://') || first.startsWith('https://')) {
    final match = RegExp(r'https?://(\[[^\]]+\]|[^:/]+)').firstMatch(first);
    return ProvisionResult(ok: true, redirect: first, ip: match?.group(1), playerId: playerId);
  }
  final detail = strings.length > 1 ? strings[1].trim() : '';
  return ProvisionResult(
    ok: false,
    reason: first.isEmpty ? 'unable_to_connect' : first,
    detail: detail.isEmpty ? null : detail,
    playerId: playerId,
  );
}
