import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/models/room.dart';
import 'package:freesounder/spotify/ui/spotify_screen.dart';

void main() {
  test('Sonos rooms are recognised and never Spotify targets', () {
    final sonos = Room.fromJson({
      'id': 'sonos-RINCON_1',
      'name': 'Den',
      'kind': 'sonos',
      'available': true,
      'url': null,
      'member_ids': <String>[],
    });
    final pi = Room.fromJson({'id': 'p1', 'name': 'Lounge', 'available': true, 'member_ids': <String>[]});
    expect(sonos.isSonos, isTrue);
    expect(pi.isSonos, isFalse);
    expect(playTargets([sonos, pi]).map((t) => t.name), ['Lounge']);
  });
}
