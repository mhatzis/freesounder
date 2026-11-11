import 'dart:async';

import 'package:flutter/material.dart';

/// Cover art with fallbacks: each URL in turn, then the FreeSounder logo.
class Artwork extends StatelessWidget {
  const Artwork({super.key, this.urls = const [], this.icon});

  final List<String> urls;

  /// Shown on the logo fallback, e.g. a radio icon.
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return _build(context, 0);
  }

  Widget _build(BuildContext context, int index) {
    if (index >= urls.length) {
      final logo = Image.asset('assets/logo.png', fit: BoxFit.cover, filterQuality: FilterQuality.medium);
      if (icon == null) {
        return logo;
      }
      return Stack(
        fit: StackFit.expand,
        children: [
          logo,
          Align(
            alignment: Alignment.bottomRight,
            child: Padding(
              padding: const EdgeInsets.all(6),
              child: Icon(icon, size: 16, color: Colors.white70),
            ),
          ),
        ],
      );
    }
    return Image.network(
      urls[index],
      key: ValueKey(urls[index]),
      fit: BoxFit.cover,
      gaplessPlayback: true,
      errorBuilder: (context, _, _) => _build(context, index + 1),
    );
  }
}

/// Rebuilds once a second while [active], for moving progress bars.
class Ticking extends StatefulWidget {
  const Ticking({super.key, required this.active, required this.builder});

  final bool active;
  final WidgetBuilder builder;

  @override
  State<Ticking> createState() => _TickingState();
}

class _TickingState extends State<Ticking> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _sync();
  }

  @override
  void didUpdateWidget(Ticking oldWidget) {
    super.didUpdateWidget(oldWidget);
    _sync();
  }

  void _sync() {
    if (widget.active && _timer == null) {
      _timer = Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) {
          setState(() {});
        }
      });
    } else if (!widget.active) {
      _timer?.cancel();
      _timer = null;
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.builder(context);
}

String formatDuration(double? seconds) {
  if (seconds == null || seconds.isNaN || seconds < 0) {
    return '--:--';
  }
  final total = seconds.round();
  final h = total ~/ 3600;
  final m = (total % 3600) ~/ 60;
  final s = total % 60;
  final mm = h > 0 ? m.toString().padLeft(2, '0') : '$m';
  return '${h > 0 ? '$h:' : ''}$mm:${s.toString().padLeft(2, '0')}';
}
