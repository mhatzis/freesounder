import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/errors.dart';
import '../models/radio_station.dart';
import '../state/playback_store.dart';
import 'station_colors.dart';
import 'music_nav.dart';

Future<void> showRadioScreen(BuildContext context) {
  return pushMusic<void>(context, 'radio', (_) => const RadioScreen());
}

/// Colourful quick filters: (label, Radio Browser tag, colour).
const radioGenres = <(String, String, Color)>[
  ('Pop', 'pop', Color(0xFFE91E63)),
  ('Rock', 'rock', Color(0xFFE53935)),
  ('Jazz', 'jazz', Color(0xFFFF8F00)),
  ('Classical', 'classical', Color(0xFF8E24AA)),
  ('Electronic', 'electronic', Color(0xFF00ACC1)),
  ('Hip hop', 'hiphop', Color(0xFF6D4C41)),
  ('Chill', 'chillout', Color(0xFF26A69A)),
  ('News', 'news', Color(0xFF1E88E5)),
  ('Talk', 'talk', Color(0xFF5E35B1)),
  ('Country', 'country', Color(0xFF7CB342)),
];

/// (label, ISO country code, colour).
const radioCountries = <(String, String, Color)>[
  ('Australia', 'AU', Color(0xFF00897B)),
  ('New Zealand', 'NZ', Color(0xFF3949AB)),
  ('UK', 'GB', Color(0xFFC62828)),
  ('USA', 'US', Color(0xFF1565C0)),
  ('Ireland', 'IE', Color(0xFF2E7D32)),
  ('France', 'FR', Color(0xFF283593)),
  ('Germany', 'DE', Color(0xFFF9A825)),
  ('Japan', 'JP', Color(0xFFAD1457)),
  ('Brazil', 'BR', Color(0xFF43A047)),
];

String flagFor(String code) => String.fromCharCodes([for (final c in code.toUpperCase().codeUnits) 0x1F1E6 + c - 0x41]);

/// Radio Browser search. The selected room's player (its group leader) plays
/// the stream itself, so the phone can leave afterwards.
class RadioScreen extends ConsumerStatefulWidget {
  const RadioScreen({super.key});

  @override
  ConsumerState<RadioScreen> createState() => _RadioScreenState();
}

class _RadioScreenState extends ConsumerState<RadioScreen> {
  final _query = TextEditingController();
  Timer? _debounce;
  List<RadioStation> _results = const [];
  List<RadioStation> _recent = const [];
  bool _loading = false;
  String? _error;
  String? _playing;
  String? _tag;
  String? _country;

  @override
  void initState() {
    super.initState();
    _search('');
    ref.read(playbackProvider.notifier).recentRadio().then((items) {
      if (mounted) {
        setState(() => _recent = items);
      }
    });
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _query.dispose();
    super.dispose();
  }

  void _onChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), () => _search(value.trim()));
  }

  Future<void> _search(String query) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await ref.read(playbackProvider.notifier).searchRadio(query, tag: _tag, country: _country);
      if (mounted) {
        setState(() => _results = results);
      }
    } on Object catch (err) {
      if (mounted) {
        setState(() => _error = userMessageFor(err));
      }
    } finally {
      if (mounted) {
        setState(() => _loading = false);
      }
    }
  }

  Future<void> _play(RadioStation station) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _playing = station.stationuuid ?? station.url);
    try {
      await ref.read(playbackProvider.notifier).playRadio(station);
      final room = ref.read(playbackProvider).activeRoom;
      messenger.showSnackBar(
        SnackBar(content: Text('Playing ${station.name} in ${room?.groupName ?? room?.name ?? 'the room'}')),
      );
    } on Object catch (err) {
      messenger.showSnackBar(SnackBar(content: Text(userMessageFor(err))));
    } finally {
      if (mounted) {
        setState(() => _playing = null);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(playbackProvider);
    final room = state.activeRoom;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final showRecent = _query.text.trim().isEmpty && _tag == null && _country == null && _recent.isNotEmpty;
    final np = state.nowPlaying;
    final onAir = np != null && np.source == 'radio' && np.isPlaying ? (np.stationName ?? np.title) : null;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Radio'),
        actions: const [MusicCloseButton()],
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(28),
          child: Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(
              room == null ? 'No room selected' : 'Plays in ${room.groupName ?? room.name}',
              key: const Key('radio-target-room'),
              style: text.labelMedium?.copyWith(color: scheme.onSurfaceVariant),
            ),
          ),
        ),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
            child: TextField(
              key: const Key('radio-search'),
              controller: _query,
              onChanged: _onChanged,
              onSubmitted: (v) => _search(v.trim()),
              textInputAction: TextInputAction.search,
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                hintText: 'Search stations',
                border: OutlineInputBorder(),
              ),
            ),
          ),
          _chips(),
          if (_loading) const LinearProgressIndicator(),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(_error!, style: TextStyle(color: scheme.error)),
            ),
          Expanded(
            child: ListView(
              key: const Key('radio-results'),
              children: [
                if (showRecent) ...[
                  _header(context, 'Recently played'),
                  for (final station in _recent) _tile(station, onAir),
                  _header(context, 'Popular near you'),
                ],
                for (final station in _results) _tile(station, onAir),
                if (!_loading && _results.isEmpty && _error == null)
                  const Padding(
                    padding: EdgeInsets.all(24),
                    child: Text('No stations found.', textAlign: TextAlign.center),
                  ),
                const Padding(
                  padding: EdgeInsets.all(16),
                  child: Text(
                    'Stations from radio-browser.info, a free community directory.',
                    textAlign: TextAlign.center,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _header(BuildContext context, String title) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
      child: Text(title, style: Theme.of(context).textTheme.titleSmall),
    );
  }

  Widget _chips() {
    Widget chip(String key, String label, Color colour, bool selected, VoidCallback onTap) {
      return Padding(
        padding: const EdgeInsets.only(right: 8),
        child: FilterChip(
          key: Key('radio-chip-$key'),
          label: Text(label),
          selected: selected,
          showCheckmark: false,
          onSelected: (_) => onTap(),
          backgroundColor: colour.withValues(alpha: 0.14),
          selectedColor: colour,
          labelStyle: TextStyle(
            color: selected ? Colors.white : HSLColor.fromColor(colour).withLightness(0.32).toColor(),
            fontWeight: FontWeight.w600,
          ),
          shape: const StadiumBorder(),
        ),
      );
    }

    return SizedBox(
      height: 44,
      child: ListView(
        key: const Key('radio-chips'),
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        children: [
          for (final (label, tag, colour) in radioGenres)
            chip('tag-$tag', label, colour, _tag == tag, () {
              setState(() => _tag = _tag == tag ? null : tag);
              _search(_query.text.trim());
            }),
          for (final (label, code, colour) in radioCountries)
            chip('country-$code', '${flagFor(code)} $label', colour, _country == code, () {
              setState(() => _country = _country == code ? null : code);
              _search(_query.text.trim());
            }),
        ],
      ),
    );
  }

  Widget _tile(RadioStation station, String? onAir) {
    final id = station.stationuuid ?? station.url;
    final busy = _playing != null && _playing == id;
    return StationCard(
      key: Key('radio-station-${station.stationuuid ?? station.name}'),
      station: station,
      busy: busy,
      live: onAir != null && onAir == station.name,
      onTap: busy ? null : () => _play(station),
    );
  }
}

/// A station as a gradient card tinted from its logo's dominant colour.
class StationCard extends StatefulWidget {
  const StationCard({super.key, required this.station, this.busy = false, this.live = false, this.onTap});

  final RadioStation station;
  final bool busy;
  final bool live;
  final VoidCallback? onTap;

  @override
  State<StationCard> createState() => _StationCardState();
}

class _StationCardState extends State<StationCard> {
  Color? _colour;

  @override
  void initState() {
    super.initState();
    _resolve();
  }

  @override
  void didUpdateWidget(StationCard old) {
    super.didUpdateWidget(old);
    if (old.station.favicon != widget.station.favicon) {
      _resolve();
    }
  }

  void _resolve() {
    final url = widget.station.favicon;
    _colour = StationColors.cached(url);
    if (_colour == null) {
      StationColors.forUrl(url).then((c) {
        if (mounted && c != null && url == widget.station.favicon) {
          setState(() => _colour = c);
        }
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final station = widget.station;
    final base = _colour ?? StationColors.fallbackFor(station.name);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 5),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 450),
        curve: Curves.easeOut,
        decoration: BoxDecoration(
          gradient: StationColors.gradientFor(base),
          borderRadius: BorderRadius.circular(18),
          boxShadow: [
            BoxShadow(
              color: base.withValues(alpha: widget.live ? 0.55 : 0.25),
              blurRadius: widget.live ? 18 : 8,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: Material(
          type: MaterialType.transparency,
          child: InkWell(
            borderRadius: BorderRadius.circular(18),
            onTap: widget.onTap,
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Row(
                children: [
                  _StationIcon(url: station.favicon),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          station.name,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 16),
                        ),
                        if (station.subtitle.isNotEmpty)
                          Text(
                            station.subtitle,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: Colors.white70, fontSize: 13),
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  if (widget.busy)
                    const SizedBox(
                      width: 22,
                      height: 22,
                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                    )
                  else if (widget.live)
                    const LiveEqualizer(key: Key('radio-live'), size: 22)
                  else
                    const Icon(Icons.play_circle_fill, color: Colors.white, size: 30),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _StationIcon extends StatelessWidget {
  const _StationIcon({this.url});

  final String? url;

  @override
  Widget build(BuildContext context) {
    final fallback = CircleAvatar(
      radius: 22,
      backgroundColor: Colors.white24,
      child: const Icon(Icons.radio, color: Colors.white),
    );
    final src = url;
    if (src == null || !src.startsWith('http')) {
      return fallback;
    }
    return ClipOval(
      child: Container(
        color: Colors.white,
        width: 44,
        height: 44,
        child: Image.network(src, width: 44, height: 44, fit: BoxFit.cover, errorBuilder: (_, _, _) => fallback),
      ),
    );
  }
}
