import 'package:flutter_test/flutter_test.dart';
import 'package:freesounder/models/room.dart';
import 'package:freesounder/spotify/ui/spotify_screen.dart';

void main() {
  const kitchen = Room(id: 'kitchen', name: 'Kitchen', source: 'spotify', state: 'playing');
  const lounge = Room(id: 'lounge', name: 'Lounge', source: 'none', state: 'idle');
  const office = Room(id: 'office', name: 'Office', source: 'spotify', state: 'paused');

  test('another room playing Spotify is found, the chosen room itself is not', () {
    const toLounge = PlayTarget(id: 'lounge', name: 'Lounge', memberIds: ['lounge']);
    expect(spotifyBusyElsewhere([kitchen, lounge, office], toLounge)?.name, 'Kitchen');
    const toKitchen = PlayTarget(id: 'kitchen', name: 'Kitchen', memberIds: ['kitchen']);
    expect(spotifyBusyElsewhere([kitchen, lounge, office], toKitchen), isNull);
  });

  test('paused Spotify elsewhere does not ask', () {
    const toLounge = PlayTarget(id: 'lounge', name: 'Lounge', memberIds: ['lounge']);
    expect(spotifyBusyElsewhere([lounge, office], toLounge), isNull);
  });
}
