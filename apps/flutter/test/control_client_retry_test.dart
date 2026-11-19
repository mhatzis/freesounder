import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/control_client.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  test('a GET on a connection the player just closed is sent again once', () async {
    var calls = 0;
    final client = ControlApiClient(
      baseUrl: 'http://10.0.0.2:8080',
      httpClient: MockClient((r) async {
        calls += 1;
        if (calls == 1) {
          throw http.ClientException('Connection reset by peer', r.url);
        }
        return http.Response(jsonEncode({'name': 'Patio', 'volume_limit': 100, 'bass': 0, 'treble': 0, 'balance': 0, 'dac': 'auto'}), 200);
      }),
    );
    final st = await client.getSettings('patio');
    expect(st.name, 'Patio');
    expect(calls, 2);
  });

  test('other client errors are not retried', () async {
    var calls = 0;
    final client = ControlApiClient(
      baseUrl: 'http://10.0.0.2:8080',
      httpClient: MockClient((r) async {
        calls += 1;
        throw http.ClientException('Connection refused', r.url);
      }),
    );
    await expectLater(client.getSettings('patio'), throwsA(anything));
    expect(calls, 1);
  });

  test('stale-connection errors are recognised', () {
    expect(isStaleConnection(http.ClientException('Connection closed before full header was received')), isTrue);
  });
}
