import 'dart:async';

import 'package:multicast_dns/multicast_dns.dart';

import '../models/player_settings.dart';

const _serviceName = '_freesounder._tcp.local';

/// Looks up FreeSounder players advertised as `_freesounder._tcp` (set-up
/// players first, then ones that still need setup).
Future<List<DiscoveredHost>> discoverControlApis({
  Duration timeout = const Duration(seconds: 3),
}) async {
  final client = MDnsClient();
  final found = <String, DiscoveredHost>{};
  try {
    await client.start();
    final ptrs = <PtrResourceRecord>[];
    await for (final ptr
        in client
            .lookup<PtrResourceRecord>(ResourceRecordQuery.serverPointer(_serviceName))
            .timeout(timeout, onTimeout: (sink) => sink.close())) {
      ptrs.add(ptr);
    }
    for (final ptr in ptrs) {
      final host = await _resolvePtr(client, ptr);
      if (host != null) {
        found[host.url] = host;
      }
    }
    final hosts = found.values.toList()
      ..sort((a, b) => (a.unclaimed ? 1 : 0).compareTo(b.unclaimed ? 1 : 0));
    return hosts;
  } on Object {
    return found.values.toList();
  } finally {
    client.stop();
  }
}

Future<DiscoveredHost?> _resolvePtr(MDnsClient client, PtrResourceRecord ptr) async {
  final domain = ptr.domainName;
  String? host;
  var port = 8080;
  await for (final srv
      in client
          .lookup<SrvResourceRecord>(ResourceRecordQuery.service(domain))
          .timeout(const Duration(seconds: 1), onTimeout: (sink) => sink.close())) {
    host = srv.target;
    port = srv.port;
    break;
  }
  if (host == null) {
    return null;
  }
  String? ip;
  await for (final record
      in client
          .lookup<IPAddressResourceRecord>(ResourceRecordQuery.addressIPv4(host))
          .timeout(const Duration(seconds: 1), onTimeout: (sink) => sink.close())) {
    ip = record.address.address;
    break;
  }
  final txt = <String, String>{};
  await for (final record
      in client
          .lookup<TxtResourceRecord>(ResourceRecordQuery.text(domain))
          .timeout(const Duration(seconds: 1), onTimeout: (sink) => sink.close())) {
    for (final line in record.text.split('\n')) {
      final eq = line.indexOf('=');
      if (eq > 0) {
        txt[line.substring(0, eq)] = line.substring(eq + 1);
      }
    }
    break;
  }
  final target = ip ?? host;
  final fallback = domain.split('.').first;
  final room = txt['room'];
  return DiscoveredHost(
    url: 'http://$target:$port',
    name: (room != null && room.isNotEmpty) ? room : (fallback.isEmpty ? 'FreeSounder' : fallback),
    state: txt['state'],
  );
}

/// Convenience: first discovered household, or null.
Future<String?> discoverControlApi({
  Duration timeout = const Duration(seconds: 3),
}) async {
  final found = await discoverControlApis(timeout: timeout);
  return found.isEmpty ? null : found.first.url;
}
