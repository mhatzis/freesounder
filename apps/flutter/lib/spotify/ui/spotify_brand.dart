import 'package:flutter/material.dart';

/// Spotify's official icon / full logo, per the Design & Branding Guidelines
/// (assets/brand/spotify/README.md): monochrome on our tinted surfaces
/// (black on light, white on dark), Spotify Green only on pure black/white,
/// and never below the minimum size (icon 21 px, full logo 70 px wide).
class SpotifyMark extends StatelessWidget {
  const SpotifyMark.icon({super.key, this.size = 24, this.onPureBackground = false}) : full = false;

  /// Icon + wordmark; [size] is the height (width is ~3.65×).
  const SpotifyMark.logo({super.key, this.size = 24, this.onPureBackground = false}) : full = true;

  static const double minIcon = 21;
  static const double minLogoWidth = 70;
  static const double logoAspect = 3432 / 940;

  final bool full;
  final double size;

  /// True only when drawn on pure #000/#FFF, where the green colourway is allowed.
  final bool onPureBackground;

  static String assetFor({required bool full, required Brightness brightness, bool green = false}) {
    final colour = green ? 'green' : (brightness == Brightness.dark ? 'white' : 'black');
    return 'assets/brand/spotify/${full ? 'logo' : 'icon'}_$colour.png';
  }

  @override
  Widget build(BuildContext context) {
    final brightness = Theme.of(context).brightness;
    final asset = assetFor(full: full, brightness: brightness, green: onPureBackground);
    if (full) {
      final height = size * logoAspect < minLogoWidth ? minLogoWidth / logoAspect : size;
      return Image.asset(asset, height: height, width: height * logoAspect, semanticLabel: 'Spotify');
    }
    final s = size < minIcon ? minIcon : size;
    return Image.asset(asset, width: s, height: s, semanticLabel: 'Spotify');
  }
}
