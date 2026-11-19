import 'package:flutter/material.dart';

/// Every screen of the "Play music" flow (the picker sheet, Radio, Music
/// library, Podcasts, Spotify and their sub-screens) is a route named
/// `music/…`, so one Close button can leave the whole flow and land back
/// where it was opened from, while the back arrow steps back one screen
/// (sub-screen → screen → picker).
const musicRoutePrefix = 'music/';

bool isMusicRoute(Route<dynamic> route) => route.settings.name?.startsWith(musicRoutePrefix) ?? false;

Future<T?> pushMusic<T>(BuildContext context, String name, WidgetBuilder builder) {
  return Navigator.of(context).push<T>(
    MaterialPageRoute<T>(
      settings: RouteSettings(name: '$musicRoutePrefix$name'),
      builder: builder,
    ),
  );
}

/// Leaves the whole music flow (all music screens and the picker).
void closeMusic(BuildContext context) {
  Navigator.of(context).popUntil((route) => !isMusicRoute(route));
}

/// The ✕ in every music screen's header.
class MusicCloseButton extends StatelessWidget {
  const MusicCloseButton({super.key});

  @override
  Widget build(BuildContext context) {
    return IconButton(
      key: const Key('music-close'),
      tooltip: 'Close',
      icon: const Icon(Icons.close_rounded),
      onPressed: () => closeMusic(context),
    );
  }
}
