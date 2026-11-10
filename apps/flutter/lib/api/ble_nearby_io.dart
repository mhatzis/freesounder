import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'package:network_info_plus/network_info_plus.dart';
import 'package:permission_handler/permission_handler.dart';

import 'improv.dart';
import 'nearby.dart';

NearbyTransport createBleNearbyTransport() => BleNearbyTransport();

/// Live Improv client over flutter_blue_plus. Never logs a Wi-Fi password.
class BleNearbyTransport implements NearbyTransport {
  final _controller = StreamController<List<NearbyPlayer>>.broadcast();
  final _found = <String, NearbyPlayer>{};
  StreamSubscription<List<ScanResult>>? _scanSub;
  BluetoothDevice? _device;
  BluetoothCharacteristic? _rpcCommand;
  BluetoothCharacteristic? _rpcResult;
  BluetoothCharacteristic? _currentState;
  BluetoothCharacteristic? _errorState;
  DeviceInfo _info = const DeviceInfo();

  @override
  Stream<List<NearbyPlayer>> get players => _controller.stream;

  @override
  Future<void> startScan() async {
    await _ensurePermissions();
    _found.clear();
    await _scanSub?.cancel();
    _scanSub = FlutterBluePlus.scanResults.listen((results) {
      for (final result in results) {
        final player = _playerFrom(result);
        if (player != null) {
          _found[player.id] = player;
        }
      }
      _controller.add(_found.values.toList());
    });
    await FlutterBluePlus.startScan(
      withServices: [Guid(improvServiceUuid)],
      timeout: const Duration(seconds: 8),
    );
  }

  @override
  Future<void> stopScan() async {
    await FlutterBluePlus.stopScan();
    await _scanSub?.cancel();
    _scanSub = null;
  }

  @override
  Future<void> connect(NearbyPlayer player) async {
    await stopScan();
    final device = BluetoothDevice.fromId(player.id);
    _device = device;
    await device.connect(timeout: const Duration(seconds: 15));
    final services = await device.discoverServices();
    BluetoothService? improv;
    for (final service in services) {
      if (service.uuid == Guid(improvServiceUuid)) {
        improv = service;
        break;
      }
    }
    if (improv == null) {
      throw StateError('Improv service not found on ${player.name}');
    }
    for (final char in improv.characteristics) {
      final uuid = char.uuid;
      if (uuid == Guid(improvRpcCommandUuid)) {
        _rpcCommand = char;
      } else if (uuid == Guid(improvRpcResultUuid)) {
        _rpcResult = char;
        await char.setNotifyValue(true);
      } else if (uuid == Guid(improvCurrentStateUuid)) {
        _currentState = char;
        await char.setNotifyValue(true);
      } else if (uuid == Guid(improvErrorStateUuid)) {
        _errorState = char;
        await char.setNotifyValue(true);
      }
    }
    if (_rpcCommand == null || _rpcResult == null || _currentState == null) {
      throw StateError('Improv characteristics missing on ${player.name}');
    }
    try {
      _info = await deviceInfo();
    } on Object {
      _info = DeviceInfo(playerId: player.id, deviceName: player.name);
    }
  }

  @override
  Future<DeviceInfo> deviceInfo() async {
    final frame = await _rpc(improvCmdDeviceInfo, const []);
    final strings = decodeImprovResult(frame);
    _info = DeviceInfo(
      firmwareName: strings.isNotEmpty ? strings[0] : 'FreeSounder',
      firmwareVersion: strings.length > 1 ? strings[1] : '',
      hardware: strings.length > 2 ? strings[2] : '',
      deviceName: strings.length > 3 ? strings[3] : '',
      playerId: strings.length > 4 ? strings[4] : _info.playerId,
    );
    return _info;
  }

  @override
  Future<List<WifiCandidate>> scanWifi() async {
    final frame = await _rpc(improvCmdScanWifi, const []);
    final strings = decodeImprovResult(frame);
    final networks = <WifiCandidate>[];
    for (var i = 0; i + 2 < strings.length; i += 3) {
      final ssid = strings[i];
      if (ssid.isEmpty) {
        continue;
      }
      networks.add(
        WifiCandidate(
          ssid: ssid,
          rssi: int.tryParse(strings[i + 1]) ?? 0,
          auth: strings[i + 2],
        ),
      );
    }
    return networks;
  }

  @override
  Future<void> identify() async {
    try {
      await _rpc(improvCmdIdentify, const []);
    } on Object {
      // Identify is best-effort (LED only).
    }
  }

  @override
  Future<ProvisionResult> provision({
    required String ssid,
    required String password,
    String? roomName,
  }) async {
    final name = roomName?.trim();
    if (name != null && name.isNotEmpty) {
      try {
        await _rpc(improvCmdDeviceName, utf8.encode(name));
      } on Object {
        // Room name over BLE is optional; adopt will set it later.
      }
    }
    final payload = encodeWifiSettings(ssid: ssid, password: password);
    final frame = await _rpc(improvCmdWifiSettings, payload, waitForState: true);
    final strings = decodeImprovResult(frame);
    final state = await _readByte(_currentState);
    final error = await _readByte(_errorState);
    if (state == improvStateProvisioned) {
      final redirect = strings.isNotEmpty ? strings.first : '';
      return ProvisionResult(
        ok: true,
        redirect: redirect,
        ip: _ipFromRedirect(redirect),
        playerId: _info.playerId,
      );
    }
    final reason = strings.isNotEmpty ? strings.first : 'unable_to_connect';
    return ProvisionResult(
      ok: false,
      reason: error == improvErrorUnableToConnect || reason.isNotEmpty ? reason : 'unable_to_connect',
      playerId: _info.playerId,
    );
  }

  @override
  Future<void> disconnect() async {
    await stopScan();
    try {
      await _device?.disconnect();
    } on Object {
      // Ignore disconnect errors after the radio has already gone quiet.
    }
    _device = null;
    _rpcCommand = null;
    _rpcResult = null;
    _currentState = null;
    _errorState = null;
  }

  @override
  Future<String?> phoneSsid() async {
    try {
      if (Platform.isAndroid) {
        await Permission.locationWhenInUse.request();
      }
      final raw = await NetworkInfo().getWifiName();
      if (raw == null || raw.isEmpty) {
        return null;
      }
      return raw.replaceAll('"', '');
    } on Object {
      return null;
    }
  }

  Future<List<int>> _rpc(
    int command,
    List<int> data, {
    bool waitForState = false,
  }) async {
    final commandChar = _rpcCommand;
    final resultChar = _rpcResult;
    if (commandChar == null || resultChar == null) {
      throw StateError('Not connected to a player');
    }
    final completer = Completer<List<int>>();
    late final StreamSubscription<List<int>> sub;
    sub = resultChar.onValueReceived.listen((value) {
      if (value.isEmpty) {
        return;
      }
      if (!completer.isCompleted) {
        completer.complete(List<int>.from(value));
      }
    });
    StreamSubscription<List<int>>? stateSub;
    if (waitForState && _currentState != null) {
      stateSub = _currentState!.onValueReceived.listen((value) {
        if (value.isEmpty || completer.isCompleted) {
          return;
        }
        // Result characteristic is notified in the same state change.
      });
    }
    try {
      final frame = encodeImprovRpc(command, data);
      // Write without logging. Split if the MTU is small.
      const chunk = 20;
      if (frame.length <= chunk) {
        await commandChar.write(frame, withoutResponse: false);
      } else {
        for (var i = 0; i < frame.length; i += chunk) {
          final end = i + chunk > frame.length ? frame.length : i + chunk;
          await commandChar.write(frame.sublist(i, end), withoutResponse: true);
        }
      }
      return await completer.future.timeout(const Duration(seconds: 45));
    } finally {
      await sub.cancel();
      await stateSub?.cancel();
    }
  }

  Future<int> _readByte(BluetoothCharacteristic? char) async {
    if (char == null) {
      return 0;
    }
    try {
      final value = await char.read();
      return value.isEmpty ? 0 : value.first;
    } on Object {
      return 0;
    }
  }

  NearbyPlayer? _playerFrom(ScanResult result) {
    final name = result.advertisementData.advName.isNotEmpty
        ? result.advertisementData.advName
        : result.device.platformName;
    if (!name.startsWith('FreeSounder-')) {
      final uuids = result.advertisementData.serviceUuids;
      final hasImprov = uuids.any((u) => u == Guid(improvServiceUuid));
      if (!hasImprov) {
        return null;
      }
    }
    final display = name.isEmpty ? 'FreeSounder' : name;
    return NearbyPlayer(
      id: result.device.remoteId.str,
      name: display,
      needsWifi: true,
      subtitle: 'Needs Wi-Fi',
    );
  }

  Future<void> _ensurePermissions() async {
    if (Platform.isAndroid) {
      await [
        Permission.bluetoothScan,
        Permission.bluetoothConnect,
        Permission.locationWhenInUse,
      ].request();
    }
  }
}

String? _ipFromRedirect(String redirect) {
  final match = RegExp(r'https?://(\[[^\]]+\]|[^:/]+)').firstMatch(redirect);
  return match?.group(1);
}
