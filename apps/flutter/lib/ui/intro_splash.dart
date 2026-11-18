import 'dart:math' as math;

import 'package:flutter/material.dart';

/// Brand gradient shared with the native launch screen
/// (assets/splash/background.png is rendered from the same stops).
const brandGradient = LinearGradient(
  begin: Alignment.topLeft,
  end: Alignment.bottomRight,
  colors: [Color(0xFF361079), Color(0xFF2157B0), Color(0xFF0AC8CC)],
);

const introCaption = 'Free the music.';
const introSubline = 'Multi-room audio, owned by you.';

/// Cold-start intro drawn over [child]: picks up exactly where the native
/// launch screen leaves off (gradient + the F at 256 pt, centred), then the
/// sound-wave arcs draw outward, the caption fades in and the overlay fades
/// away. ~1.4 s, tap to skip. Created once per process, so resuming the app
/// never shows it again; skipped when the OS asks for reduced motion.
class IntroSplash extends StatefulWidget {
  const IntroSplash({
    super.key,
    required this.child,
    this.enabled = true,
    this.duration = const Duration(milliseconds: 1400),
  });

  final Widget child;
  final bool enabled;
  final Duration duration;

  @override
  State<IntroSplash> createState() => _IntroSplashState();
}

class _IntroSplashState extends State<IntroSplash> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: widget.duration);
  bool _done = false;

  @override
  void initState() {
    super.initState();
    _done = !widget.enabled;
    _c.addStatusListener((s) {
      if (s == AnimationStatus.completed && mounted) {
        setState(() => _done = true);
      }
    });
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_done || _c.isAnimating) {
      return;
    }
    if (MediaQuery.maybeDisableAnimationsOf(context) ?? false) {
      _done = true;
    } else {
      _c.forward();
    }
  }

  void _skip() {
    if (_done) return;
    // Jump to the fade-out and finish quickly.
    final from = math.max(_c.value, 0.86);
    _c.value = from;
    _c.animateTo(1, duration: const Duration(milliseconds: 180)).ignore();
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_done) {
      return widget.child;
    }
    return Stack(
      fit: StackFit.expand,
      children: [
        widget.child,
        GestureDetector(
          key: const Key('intro-splash'),
          behavior: HitTestBehavior.opaque,
          onTap: _skip,
          child: AnimatedBuilder(animation: _c, builder: (context, _) => _frame(context, _c.value)),
        ),
      ],
    );
  }

  static double _seg(double t, double a, double b) =>
      Curves.easeOutCubic.transform(((t - a) / (b - a)).clamp(0.0, 1.0));

  Widget _frame(BuildContext context, double t) {
    final fadeOut = 1 - _seg(t, 0.86, 1.0);
    final scale = 1 + 0.06 * _seg(t, 0.0, 0.5) - 0.02 * _seg(t, 0.5, 0.86);
    final caption = _seg(t, 0.38, 0.66);
    final subline = _seg(t, 0.52, 0.80);
    final text = Theme.of(context).textTheme;
    return Opacity(
      opacity: fadeOut,
      child: DecoratedBox(
        decoration: const BoxDecoration(gradient: brandGradient),
        child: Stack(
          fit: StackFit.expand,
          children: [
            Center(
              child: Transform.scale(
                scale: scale,
                child: SizedBox.square(
                  dimension: 256,
                  child: Stack(
                    fit: StackFit.expand,
                    children: [
                      Image.asset('assets/splash/mark_f.png', filterQuality: FilterQuality.medium),
                      CustomPaint(
                        painter: SoundWavesPainter([
                          for (var i = 0; i < 3; i++) _seg(t, 0.06 + 0.12 * i, 0.40 + 0.12 * i),
                        ]),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            Align(
              alignment: const Alignment(0, 0.52),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Opacity(
                    opacity: caption,
                    child: Transform.translate(
                      offset: Offset(0, 12 * (1 - caption)),
                      child: Text(
                        introCaption,
                        key: const Key('intro-caption'),
                        style: text.headlineSmall?.copyWith(
                          color: Colors.white,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 0.2,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 6),
                  Opacity(
                    opacity: subline,
                    child: Text(
                      introSubline,
                      style: text.bodyMedium?.copyWith(color: Colors.white.withValues(alpha: 0.85)),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// The logo's three arcs (fitted from assets/logo.png on its 1024 grid:
/// centre 528,508; radii 104/194/283; stroke 38; ±62°), each drawn outward
/// from its middle by its own progress 0..1.
class SoundWavesPainter extends CustomPainter {
  SoundWavesPainter(this.progress);

  final List<double> progress;

  static const _radii = [104.0, 194.0, 283.0];
  static const _half = 62 * math.pi / 180;

  @override
  void paint(Canvas canvas, Size size) {
    final k = size.width / 1024;
    final paint = Paint()
      ..color = Colors.white
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeWidth = 38 * k;
    final centre = Offset(528 * k, 508 * k);
    for (var i = 0; i < _radii.length; i++) {
      final p = progress[i];
      if (p <= 0) continue;
      final r = _radii[i] * k;
      final sweep = _half * p;
      paint.color = Colors.white.withValues(alpha: (p * 1.6).clamp(0.0, 1.0));
      canvas.drawArc(Rect.fromCircle(center: centre, radius: r), -sweep, 2 * sweep, false, paint);
    }
  }

  @override
  bool shouldRepaint(SoundWavesPainter old) => old.progress.toString() != progress.toString();
}
