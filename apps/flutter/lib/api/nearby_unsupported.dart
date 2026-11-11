import 'nearby.dart';

NearbyTransport createBleNearbyTransport() => _UnsupportedNearbyTransport();

class _UnsupportedNearbyTransport implements NearbyTransport {
  @override
  Stream<List<NearbyPlayer>> get players => const Stream.empty();

  @override
  Future<void> startScan() async {}

  @override
  Future<void> stopScan() async {}

  @override
  Future<void> connect(NearbyPlayer player) async {
    throw UnsupportedError('Bluetooth setup is not available on this platform.');
  }

  @override
  Future<DeviceInfo> deviceInfo() async => const DeviceInfo();

  @override
  Future<List<WifiCandidate>> scanWifi() async => const [];

  @override
  Future<void> identify() async {}

  @override
  Future<ProvisionResult> provision({
    required String ssid,
    required String password,
    String? roomName,
  }) async {
    throw UnsupportedError('Bluetooth setup is not available on this platform.');
  }

  @override
  Future<void> disconnect() async {}

  @override
  Future<String?> phoneSsid() async => null;

  @override
  String get lastDebug => 'Bluetooth is not supported on this platform';

  @override
  Future<bool> setWifiCountry(String country) async => false;
}
