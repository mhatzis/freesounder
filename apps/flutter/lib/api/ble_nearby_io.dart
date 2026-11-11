import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'package:network_info_plus/network_info_plus.dart';
import 'package:permission_handler/permission_handler.dart';

import 'ble_steps.dart';
import 'improv.dart';
import 'nearby.dart';

NearbyTransport createBleNearbyTransport() => BleNearbyTransport();

/// Live Improv client over flutter_blue_plus. Never logs a Wi-Fi password.
///
/// Every step has its own timeout and fails with a [NearbyException] naming
/// the step, so the UI never spins forever and never shows a raw exception.
class BleNearbyTransport implements NearbyTransport {
  static const connectTimeout = Duration(seconds: 15);
  static const discoverTimeout = Duration(seconds: 15);
  static const notifyTimeout = Duration(seconds: 10);
  static const deviceInfoTimeout = Duration(seconds: 10);
  static const scanWifiTimeout = Duration(seconds: 30);
  // The player unblocks the radio, rescans, joins (up to 40 s) and waits
  // for DHCP (up to 15 s) before it answers.
  static const sendWifiTimeout = Duration(seconds: 120);
  static const countryTimeout = Duration(seconds: 10);
  static const nameTimeout = Duration(seconds: 10);

  final _controller = StreamController<List<NearbyPlayer>>.broadcast();
  final _found = <String, NearbyPlayer>{};
  StreamSubscription<List<ScanResult>>? _scanSub;
  BluetoothDevice? _device;
  BluetoothCharacteristic? _rpcCommand;
  BluetoothCharacteristic? _rpcResult;
  BluetoothCharacteristic? _currentState;
  BluetoothCharacteristic? _errorState;
  StreamSubscription<List<int>>? _stateSub;
  DeviceInfo _info = const DeviceInfo();
  int _lastState = 0;
  String _lastDebug = '';

  @override
  Stream<List<NearbyPlayer>> get players => _controller.stream;

  @override
  String get lastDebug => _lastDebug;

  void _onStep(BleStep step, String? error) {
    _lastDebug = error == null ? '${step.label}…' : '${step.label} failed: $error';
  }

  @override
  Future<void> startScan() async {
    _onStep(BleStep.radio, null);
    await _ensurePermissions();
    try {
      await awaitRadioOn(FlutterBluePlus.adapterState.map(_radio));
    } on NearbyException catch (err) {
      _onStep(BleStep.radio, err.detail);
      rethrow;
    }
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
    await runStep(
      BleStep.scan,
      () => FlutterBluePlus.startScan(
        withServices: [Guid(improvServiceUuid)],
        timeout: const Duration(seconds: 8),
      ),
      timeout: const Duration(seconds: 10),
      onStep: _onStep,
    );
    _lastDebug = 'Scanning: ${_found.length} found';
  }

  @override
  Future<void> stopScan() async {
    try {
      await FlutterBluePlus.stopScan();
    } on Object {
      // Not scanning, or the radio went away. Nothing to stop.
    }
    await _scanSub?.cancel();
    _scanSub = null;
  }

  @override
  Future<void> connect(NearbyPlayer player) async {
    await stopScan();
    try {
      await _connectOnce(player);
    } on NearbyException catch (err) {
      if (err.step != BleStep.connect && err.step != BleStep.discover) {
        rethrow;
      }
      // One quiet retry: iOS sometimes drops the first link or returns an
      // empty service list straight after connecting.
      await _teardown();
      await Future<void>.delayed(const Duration(milliseconds: 600));
      await _connectOnce(player);
    }
    _info = DeviceInfo(playerId: player.id, deviceName: player.name);
    _lastDebug = 'Connected (MTU ${_device?.mtuNow ?? 23}, state $_lastState)';
  }

  Future<void> _connectOnce(NearbyPlayer player) async {
    final device = BluetoothDevice.fromId(player.id);
    _device = device;
    await runStep(
      BleStep.connect,
      () => device.connect(timeout: connectTimeout),
      timeout: connectTimeout + const Duration(seconds: 2),
      onStep: _onStep,
    );
    final services = await runStep(
      BleStep.discover,
      () => device.discoverServices(timeout: discoverTimeout.inSeconds),
      timeout: discoverTimeout + const Duration(seconds: 2),
      onStep: _onStep,
    );
    BluetoothService? improv;
    for (final service in services) {
      if (service.uuid == Guid(improvServiceUuid)) {
        improv = service;
        break;
      }
    }
    if (improv == null) {
      _onStep(BleStep.discover, 'Improv service missing (${services.length} services)');
      throw NearbyException(
        BleStep.discover,
        friendlyStepMessage(BleStep.discover),
        detail: 'Improv service $improvServiceUuid not found',
      );
    }
    for (final char in improv.characteristics) {
      final uuid = char.uuid;
      if (uuid == Guid(improvRpcCommandUuid)) {
        _rpcCommand = char;
      } else if (uuid == Guid(improvRpcResultUuid)) {
        _rpcResult = char;
      } else if (uuid == Guid(improvCurrentStateUuid)) {
        _currentState = char;
      } else if (uuid == Guid(improvErrorStateUuid)) {
        _errorState = char;
      }
    }
    if (_rpcCommand == null || _rpcResult == null || _currentState == null) {
      throw NearbyException(
        BleStep.discover,
        friendlyStepMessage(BleStep.discover),
        detail: 'characteristics missing: '
            '${[
              if (_rpcCommand == null) 'rpc-command',
              if (_rpcResult == null) 'rpc-result',
              if (_currentState == null) 'state',
            ].join(', ')}',
      );
    }
    await runStep(
      BleStep.notify,
      () async {
        await _rpcResult!.setNotifyValue(true, timeout: notifyTimeout.inSeconds);
        await _currentState!.setNotifyValue(true, timeout: notifyTimeout.inSeconds);
        await _errorState?.setNotifyValue(true, timeout: notifyTimeout.inSeconds);
      },
      timeout: notifyTimeout * 3,
      onStep: _onStep,
    );
    await _stateSub?.cancel();
    _stateSub = _currentState!.onValueReceived.listen((value) {
      if (value.isNotEmpty) {
        _lastState = value.first;
      }
    });
    _lastState = await _readByte(_currentState);
  }

  @override
  Future<DeviceInfo> deviceInfo() async {
    final frame = await _rpc(BleStep.deviceInfo, improvCmdDeviceInfo, const [], deviceInfoTimeout);
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
    final frame = await _rpc(BleStep.scanWifi, improvCmdScanWifi, const [], scanWifiTimeout);
    return parseWifiScan(decodeImprovResult(frame));
  }

  @override
  Future<bool> setWifiCountry(String country) async {
    try {
      await _rpc(BleStep.scanWifi, improvCmdWifiCountry, encodeImprovStrings([country]), countryTimeout);
      return true;
    } on Object {
      // Older players do not know the command; they keep their default.
      return false;
    }
  }

  @override
  Future<void> identify() async {
    try {
      await _rpc(BleStep.deviceInfo, improvCmdIdentify, const [], deviceInfoTimeout);
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
        await _rpc(BleStep.sendWifi, improvCmdDeviceName, utf8.encode(name), nameTimeout);
      } on Object {
        // Room name over BLE is optional; adopt will set it later.
      }
    }
    final payload = encodeWifiSettings(ssid: ssid, password: password);
    // The RPC result decides success: the player may stop advertising and
    // drop the link right after it joins Wi-Fi, so never read state after.
    final frame = await _rpc(BleStep.sendWifi, improvCmdWifiSettings, payload, sendWifiTimeout);
    final result = provisionResultFrom(decodeImprovResult(frame), playerId: _info.playerId);
    _lastDebug = result.ok
        ? 'Wi-Fi sent: joined'
        : 'Wi-Fi sent: ${result.reason}${result.detail == null ? '' : ' (${result.detail})'}';
    return result;
  }

  @override
  Future<void> disconnect() async {
    await stopScan();
    await _teardown();
  }

  Future<void> _teardown() async {
    await _stateSub?.cancel();
    _stateSub = null;
    try {
      await _device?.disconnect().timeout(const Duration(seconds: 5));
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

  Future<List<int>> _rpc(BleStep step, int command, List<int> data, Duration timeout) async {
    final commandChar = _rpcCommand;
    final resultChar = _rpcResult;
    if (commandChar == null || resultChar == null) {
      throw NearbyException(step, 'Not connected to the player. Tap it to try again.',
          detail: 'no GATT session');
    }
    return runStep(step, () async {
      final completer = Completer<List<int>>();
      final sub = resultChar.onValueReceived.listen((value) {
        if (value.isNotEmpty && !completer.isCompleted) {
          completer.complete(List<int>.from(value));
        }
      });
      StreamSubscription<List<int>>? errorSub;
      final errorChar = _errorState;
      if (errorChar != null) {
        errorSub = errorChar.onValueReceived.listen((value) {
          if (value.isEmpty || value.first == improvErrorNone || completer.isCompleted) {
            return;
          }
          final code = value.first;
          final failure = NearbyException(step, friendlyImprovError(code),
              detail: 'Improv error 0x${code.toRadixString(16).padLeft(2, '0')}');
          if (code == improvErrorUnableToConnect) {
            // The reason string arrives on the result characteristic; give it
            // a moment instead of failing with a generic message.
            Future<void>.delayed(const Duration(seconds: 3), () {
              if (!completer.isCompleted) {
                completer.completeError(failure);
              }
            });
            return;
          }
          completer.completeError(failure);
        });
      }
      try {
        final frame = encodeImprovRpc(command, data);
        final chunk = writeChunkSize(_device?.mtuNow ?? 23);
        for (var i = 0; i < frame.length; i += chunk) {
          final end = i + chunk > frame.length ? frame.length : i + chunk;
          await commandChar.write(frame.sublist(i, end), withoutResponse: false, timeout: 10);
        }
        return await completer.future;
      } finally {
        await sub.cancel();
        await errorSub?.cancel();
      }
    }, timeout: timeout, onStep: _onStep);
  }

  Future<int> _readByte(BluetoothCharacteristic? char) async {
    if (char == null) {
      return 0;
    }
    try {
      final value = await char.read(timeout: 5);
      return value.isEmpty ? 0 : value.first;
    } on Object {
      return 0;
    }
  }

  NearbyPlayer? _playerFrom(ScanResult result) {
    final name = result.advertisementData.advName.isNotEmpty
        ? result.advertisementData.advName
        : result.device.platformName;
    final uuids = result.advertisementData.serviceUuids;
    final hasImprov = uuids.any((u) => u == Guid(improvServiceUuid));
    if (!hasImprov && !name.startsWith('FreeSounder-') && !name.startsWith('FS-')) {
      return null;
    }
    return NearbyPlayer(
      id: result.device.remoteId.str,
      name: displayNameForAdvert(name),
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

RadioState _radio(BluetoothAdapterState state) {
  switch (state) {
    case BluetoothAdapterState.unknown:
      return RadioState.unknown;
    case BluetoothAdapterState.unavailable:
      return RadioState.unavailable;
    case BluetoothAdapterState.unauthorized:
      return RadioState.unauthorized;
    case BluetoothAdapterState.turningOn:
      return RadioState.turningOn;
    case BluetoothAdapterState.on:
      return RadioState.on;
    case BluetoothAdapterState.turningOff:
      return RadioState.turningOff;
    case BluetoothAdapterState.off:
      return RadioState.off;
  }
}
