import 'dart:async';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

/// Dominant, card-worthy colour of a station logo (vivid buckets win over
/// greys, whites and blacks), cached per URL. Falls back to a stable hue
/// derived from the station name so every card is colourful.
class StationColors {
  StationColors._();

  static final Map<String, Color?> _cache = {};
  static final Map<String, Future<Color?>> _pending = {};

  static Color fallbackFor(String name) {
    var h = 0;
    for (final c in name.codeUnits) {
      h = (h * 31 + c) & 0x7fffffff;
    }
    return HSLColor.fromAHSL(1, (h % 360).toDouble(), 0.62, 0.48).toColor();
  }

  static Color? cached(String? url) => url == null ? null : _cache[url];

  static Future<Color?> forUrl(String? url) {
    if (url == null || !url.startsWith('http')) {
      return Future.value(null);
    }
    if (_cache.containsKey(url)) {
      return Future.value(_cache[url]);
    }
    return _pending[url] ??= _load(url).then((c) {
      _cache[url] = c;
      _pending.remove(url);
      return c;
    });
  }

  static Future<Color?> _load(String url) async {
    final done = Completer<ui.Image?>();
    final stream = ResizeImage(
      NetworkImage(url),
      width: 48,
      height: 48,
      policy: ResizeImagePolicy.fit,
    ).resolve(ImageConfiguration.empty);
    late final ImageStreamListener listener;
    listener = ImageStreamListener(
      (info, _) {
        if (!done.isCompleted) done.complete(info.image.clone());
        stream.removeListener(listener);
      },
      onError: (_, _) {
        if (!done.isCompleted) done.complete(null);
        stream.removeListener(listener);
      },
    );
    stream.addListener(listener);
    final image = await done.future.timeout(const Duration(seconds: 6), onTimeout: () => null);
    if (image == null) {
      return null;
    }
    try {
      final data = await image.toByteData(format: ui.ImageByteFormat.rawRgba);
      return data == null ? null : dominantFromRgba(data.buffer.asUint8List());
    } finally {
      image.dispose();
    }
  }

  /// Pick the most "present and vivid" colour from raw RGBA pixels.
  static Color? dominantFromRgba(Uint8List rgba) {
    final weight = <int, double>{};
    final sums = <int, List<int>>{};
    for (var i = 0; i + 3 < rgba.length; i += 4) {
      final r = rgba[i], g = rgba[i + 1], b = rgba[i + 2], a = rgba[i + 3];
      if (a < 128) continue;
      final hsl = HSLColor.fromColor(Color.fromARGB(255, r, g, b));
      if (hsl.lightness > 0.92 || hsl.lightness < 0.08) continue;
      final key = (r >> 5) << 6 | (g >> 5) << 3 | (b >> 5);
      // Saturated mid-tones count most; greys barely count.
      weight[key] = (weight[key] ?? 0) + 0.15 + hsl.saturation * (1 - (hsl.lightness - 0.5).abs());
      final s = sums.putIfAbsent(key, () => [0, 0, 0, 0]);
      s[0] += r;
      s[1] += g;
      s[2] += b;
      s[3] += 1;
    }
    if (weight.isEmpty) {
      return null;
    }
    final best = weight.entries.reduce((a, b) => a.value >= b.value ? a : b).key;
    final s = sums[best]!;
    final colour = Color.fromARGB(255, s[0] ~/ s[3], s[1] ~/ s[3], s[2] ~/ s[3]);
    final hsl = HSLColor.fromColor(colour);
    if (hsl.saturation < 0.12) {
      return null; // a grey logo: the name hue looks better
    }
    // Keep white text readable on the card.
    return hsl.withLightness(hsl.lightness.clamp(0.30, 0.52)).withSaturation(hsl.saturation.clamp(0.45, 0.9)).toColor();
  }

  /// Card gradient: deep shade → the colour → a hue-shifted highlight.
  static LinearGradient gradientFor(Color base) {
    final hsl = HSLColor.fromColor(base);
    return LinearGradient(
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
      colors: [
        hsl.withLightness((hsl.lightness - 0.14).clamp(0.12, 1)).toColor(),
        base,
        hsl.withHue((hsl.hue + 28) % 360).withLightness((hsl.lightness + 0.08).clamp(0, 0.6)).toColor(),
      ],
      stops: const [0, 0.55, 1],
    );
  }
}

/// Three bouncing bars: "this station is on air in your room".
class LiveEqualizer extends StatefulWidget {
  const LiveEqualizer({super.key, this.color = Colors.white, this.size = 20, this.animate = true});

  final Color color;
  final double size;

  /// False in tests / reduced motion: static bars.
  final bool animate;

  @override
  State<LiveEqualizer> createState() => _LiveEqualizerState();
}

class _LiveEqualizerState extends State<LiveEqualizer> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 900));

  @override
  void initState() {
    super.initState();
  }

  @override
  void didUpdateWidget(LiveEqualizer old) {
    super.didUpdateWidget(old);
    _maybeRun();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _maybeRun();
  }

  void _maybeRun() {
    final reduce = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    if (widget.animate && !reduce) {
      _c.repeat();
    } else {
      _c.stop();
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final reduce = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    return Semantics(
      label: 'Playing now',
      child: SizedBox.square(
        dimension: widget.size,
        child: AnimatedBuilder(
          animation: _c,
          builder: (context, _) => CustomPaint(painter: _BarsPainter(reduce ? 0.3 : _c.value, widget.color)),
        ),
      ),
    );
  }
}

class _BarsPainter extends CustomPainter {
  _BarsPainter(this.t, this.color);

  final double t;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..color = color;
    const phases = [0.0, 0.37, 0.71];
    final w = size.width / 5;
    for (var i = 0; i < 3; i++) {
      final x = (t + phases[i]) % 1.0;
      final level = 0.25 + 0.75 * (x < 0.5 ? x * 2 : (1 - x) * 2);
      final h = size.height * level;
      canvas.drawRRect(
        RRect.fromRectAndRadius(Rect.fromLTWH(w * (i * 2), size.height - h, w, h), Radius.circular(w / 2)),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(_BarsPainter old) => old.t != t || old.color != color;
}
