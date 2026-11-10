import 'package:flutter/material.dart';

/// Design tokens for the FreeSounder controller.
/// Colours follow Material 3 seed generation from the logo's indigo-to-teal
/// palette, with a single seed so contrast stays with the platform theme.
abstract final class FsTokens {
  static const double railBreakpoint = 900;
  static const double railWidth = 340;
  static const double sheetCollapsed = 0.14;
  static const double sheetHalf = 0.5;
  static const double sheetFull = 0.92;

  /// Mid-tone sampled from the logo gradient (indigo → teal).
  static const Color seed = Color(0xFF2157B0);
  static const Color indigo = Color(0xFF2C2878);
  static const Color teal = Color(0xFF0EC4C8);

  static ThemeData light() => _theme(Brightness.light);

  static ThemeData dark() => _theme(Brightness.dark);

  static ThemeData _theme(Brightness brightness) {
    final scheme = ColorScheme.fromSeed(seedColor: seed, brightness: brightness);
    final base = ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      visualDensity: VisualDensity.standard,
    );
    return base.copyWith(
      appBarTheme: AppBarTheme(
        centerTitle: false,
        scrolledUnderElevation: 0,
        backgroundColor: scheme.surface,
        foregroundColor: scheme.onSurface,
      ),
      sliderTheme: const SliderThemeData(
        trackHeight: 3,
        thumbShape: RoundSliderThumbShape(enabledThumbRadius: 7),
      ),
      chipTheme: ChipThemeData(
        side: BorderSide.none,
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 0),
        labelStyle: base.textTheme.labelMedium,
      ),
    );
  }
}
