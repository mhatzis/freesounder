import 'dart:async';

import 'package:multicast_dns/multicast_dns.dart';

const _serviceName = '_freesounder._tcp.local';

/// Looks up a household control API advertised as `_freesounder._tcp`.
Future<String?> discoverControlApi({
  Duration timeout = const Duration(seconds: 3),
}) async {
  final client = MDnsClient();
  try {
    await client.start();
    final ptrs = <PtrResourceRecord>[];
    await for (final ptr
        in client
            .lookup<PtrResourceRecord>(ResourceRecordQuery.serverPointer(_serviceName))
            .timeout(timeout, onTimeout: (sink) => sink.close())) {
      ptrs.add(ptr);
      break;
    }
    if (ptrs.isEmpty) {
      return null;
    }
    final domain = ptrs.first.domainName;
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
    return 'http://$target:$port';
  } on Object {
    return null;
  } finally {
    client.stop();
  }
}
