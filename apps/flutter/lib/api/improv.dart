/// Improv Wi-Fi BLE framing used by the app-side client.
library;

import 'dart:convert';

const improvServiceUuid = '00467768-6228-2272-4663-277478268000';
const improvCurrentStateUuid = '00467768-6228-2272-4663-277478268001';
const improvErrorStateUuid = '00467768-6228-2272-4663-277478268002';
const improvRpcCommandUuid = '00467768-6228-2272-4663-277478268003';
const improvRpcResultUuid = '00467768-6228-2272-4663-277478268004';
const improvCapabilitiesUuid = '00467768-6228-2272-4663-277478268005';
const improvServiceDataUuid = '00004677-0000-1000-8000-00805f9b34fb';

const improvCmdWifiSettings = 0x01;
const improvCmdIdentify = 0x02;
const improvCmdDeviceInfo = 0x03;
const improvCmdScanWifi = 0x04;
const improvCmdHostname = 0x05;
const improvCmdDeviceName = 0x06;

/// FreeSounder extension: ISO 3166 Wi-Fi country as one Improv string.
/// Older players answer UNKNOWN_RPC, which the app ignores.
const improvCmdWifiCountry = 0x80;

const improvStateAuthorized = 0x02;
const improvStateProvisioning = 0x03;
const improvStateProvisioned = 0x04;

const improvErrorNone = 0x00;
const improvErrorUnableToConnect = 0x03;

int improvChecksum(List<int> data) => data.fold<int>(0, (a, b) => a + b) & 0xFF;

List<int> encodeImprovRpc(int command, List<int> data) {
  final body = <int>[command, data.length, ...data];
  return [...body, improvChecksum(body)];
}

List<int> encodeImprovStrings(List<String> values) {
  final data = <int>[];
  for (final value in values) {
    final bytes = utf8.encode(value);
    final slice = bytes.length > 255 ? bytes.sublist(0, 255) : bytes;
    data.add(slice.length);
    data.addAll(slice);
  }
  return data;
}

List<int> encodeWifiSettings({required String ssid, required String password}) {
  final s = utf8.encode(ssid);
  final p = utf8.encode(password);
  return [s.length, ...s, p.length, ...p];
}

({int command, List<int> data})? tryParseImprovRpc(List<int> buffer) {
  if (buffer.length < 3) {
    return null;
  }
  final length = buffer[1];
  final frameLen = 3 + length;
  if (buffer.length < frameLen) {
    return null;
  }
  final frame = buffer.sublist(0, frameLen);
  if (improvChecksum(frame.sublist(0, frame.length - 1)) != frame.last) {
    throw const FormatException('invalid Improv checksum');
  }
  return (command: frame[0], data: frame.sublist(2, frame.length - 1));
}

List<String> decodeImprovStrings(List<int> data) {
  final out = <String>[];
  var i = 0;
  while (i < data.length) {
    final length = data[i];
    i += 1;
    if (i + length > data.length) {
      break;
    }
    out.add(utf8.decode(data.sublist(i, i + length), allowMalformed: true));
    i += length;
  }
  return out;
}

List<String> decodeImprovResult(List<int> frame) {
  final parsed = tryParseImprovRpc(frame);
  if (parsed == null) {
    return const [];
  }
  return decodeImprovStrings(parsed.data);
}

const improvStateAuthorizationRequired = 0x01;
const improvErrorInvalidRpc = 0x01;
const improvErrorUnknownRpc = 0x02;
const improvErrorNotAuthorized = 0x04;
const improvErrorBadHostname = 0x05;

/// Bytes per GATT write: ATT MTU minus the 3-byte header, never below the
/// 20 bytes every link supports, capped so a slow link does not stall.
int writeChunkSize(int mtu) {
  final size = mtu - 3;
  if (size < 20) {
    return 20;
  }
  return size > 182 ? 182 : size;
}

/// Friendly text for an Improv error-state byte.
String friendlyImprovError(int code) {
  switch (code) {
    case improvErrorInvalidRpc:
    case improvErrorUnknownRpc:
      return 'The player did not understand the app. Update the app and the player, then try again.';
    case improvErrorUnableToConnect:
      return 'The player could not join that network. Check the password and try again.';
    case improvErrorNotAuthorized:
      return 'The player needs you to confirm setup on the device first.';
    case improvErrorBadHostname:
      return 'That name is not allowed. Try a shorter name using letters and numbers.';
    default:
      return 'The player reported a setup problem. Try again.';
  }
}

/// Two-letter Wi-Fi country from a locale name such as `en_AU` or
/// `en-AU`; [fallback] when the locale has no region.
String wifiCountryFromLocale(String localeName, {String fallback = 'AU'}) {
  final base = localeName.split('.').first.split('@').first;
  final parts = base.split(RegExp('[_-]'));
  for (final part in parts.skip(1)) {
    if (RegExp(r'^[A-Za-z]{2}$').hasMatch(part)) {
      return part.toUpperCase();
    }
  }
  return fallback;
}
