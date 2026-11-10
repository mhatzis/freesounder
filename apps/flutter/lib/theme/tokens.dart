import 'package:flutter/material.dart';

/// Design tokens for the freesounder controller.
/// Colours follow Material 3 seed generation — no hardcoded brand palette
/// beyond a single seed, so contrast stays with the platform theme.
abstract final class FsTokens {
  static const double railBreakpoint = 900;
  static const double railWidth = 320;
  static const double sheetCollapsed = 0.14;
  static const double sheetHalf = 0.5;
  static const double sheetFull = 0.92;
  static const Color seed = Color(0xFF1B6B5A);

  static ThemeData light() {
    final scheme = ColorScheme.fromSeed(seedColor: seed, brightness: Brightness.light);
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      visualDensity: VisualDensity.standard,
    );
  }

  static ThemeData dark() {
    final scheme = ColorScheme.fromSeed(seedColor: seed, brightness: Brightness.dark);
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      visualDensity: VisualDensity.standard,
    );
  }
}
