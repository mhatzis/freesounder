import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/api/control_client.dart';

void main() {
  test('a speaker waiting for spotify.com/pair carries its code', () {
    final info = PlayerSpotify.fromJson({
      'leader_id': 'lounge',
      'logged_in': false,
      'pairing': {'code': 'ABCD-EFGH', 'url': 'https://www.spotify.com/pair?code=ABCDEFGH', 'expires_at': 'x'},
    });
    expect(info.loggedIn, isFalse);
    expect(info.pairing!.code, 'ABCD-EFGH');
    expect(info.pairing!.url, contains('code=ABCDEFGH'));
  });

  test('older players (no pairing field) still parse', () {
    final info = PlayerSpotify.fromJson({'leader_id': 'a', 'logged_in': true, 'username': 'me'});
    expect(info.pairing, isNull);
    expect(info.loggedIn, isTrue);
  });
}
