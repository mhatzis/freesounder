import 'nearby.dart';
import 'nearby_unsupported.dart' if (dart.library.io) 'ble_nearby_io.dart';

NearbyTransport createPlatformNearbyTransport() => createBleNearbyTransport();
