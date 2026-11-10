import 'dart:async';

import 'package:multicast_dns/multicast_dns.dart';

import '../models/player_settings.dart';

const _serviceName = '_freesounder._tcp.local';

/// Looks up household control APIs advertised as `_freesounder._tcp`.
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
    return found.values.toList();
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
  final target = ip ?? host;
  final name = domain.split('.').first;
  return DiscoveredHost(url: 'http://$target:$port', name: name.isEmpty ? 'FreeSounder' : name);
}

/// Convenience: first discovered household, or null.
Future<String?> discoverControlApi({
  Duration timeout = const Duration(seconds: 3),
}) async {
  final found = await discoverControlApis(timeout: timeout);
  return found.isEmpty ? null : found.first.url;
}
