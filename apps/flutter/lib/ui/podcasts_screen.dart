import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/errors.dart';
import '../models/podcast.dart';
import '../state/playback_store.dart';
import 'artwork.dart';
import 'music_nav.dart';

Future<void> showPodcastsScreen(BuildContext context) {
  return pushMusic<void>(context, 'podcasts', (_) => const PodcastsScreen());
}

String _ago(DateTime? date) {
  if (date == null) {
    return '';
  }
  final days = DateTime.now().difference(date).inDays;
  if (days <= 0) {
    return 'Today';
  }
  if (days == 1) {
    return 'Yesterday';
  }
  if (days < 7) {
    return '$days days ago';
  }
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  final year = date.year == DateTime.now().year ? '' : ' ${date.year}';
  return '${date.day} ${months[date.month - 1]}$year';
}

/// Podcasts: search the Apple Podcasts directory (no account), subscribe, and
/// play episodes on the room's speakers. Subscriptions and where you stopped
/// are shared by every phone in the home.
class PodcastsScreen extends ConsumerStatefulWidget {
  const PodcastsScreen({super.key});

  @override
  ConsumerState<PodcastsScreen> createState() => _PodcastsScreenState();
}

class _PodcastsScreenState extends ConsumerState<PodcastsScreen> {
  final _query = TextEditingController();
  Timer? _debounce;
  List<Podcast>? _subs;
  List<Podcast>? _results;
  bool _searching = false;
  String? _error;

  PlaybackStore get _store => ref.read(playbackProvider.notifier);

  @override
  void initState() {
    super.initState();
    _loadSubs();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _query.dispose();
    super.dispose();
  }

  Future<void> _loadSubs() async {
    try {
      final subs = await _store.podcastSubscriptions();
      if (mounted) {
        setState(() => _subs = subs);
      }
    } on Object catch (e) {
      if (mounted) {
        setState(() {
          _subs = const [];
          _error = userMessageFor(e);
        });
      }
    }
  }

  void _onChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 450), () => _search(value.trim()));
  }

  Future<void> _search(String q) async {
    if (q.isEmpty) {
      setState(() => _results = null);
      return;
    }
    setState(() {
      _searching = true;
      _error = null;
    });
    try {
      final r = await _store.searchPodcasts(q);
      if (mounted && _query.text.trim() == q) {
        setState(() => _results = r);
      }
    } on Object catch (e) {
      if (mounted) {
        setState(() => _error = userMessageFor(e));
      }
    } finally {
      if (mounted) {
        setState(() => _searching = false);
      }
    }
  }

  Future<void> _open(Podcast p) async {
    await pushMusic<void>(context, 'podcasts/show', (_) => PodcastScreen(podcast: p));
    await _loadSubs();
  }

  @override
  Widget build(BuildContext context) {
    final room = ref.watch(playbackProvider.select((s) => s.activeRoom));
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final subs = _subs;
    final results = _results;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Podcasts'),
        actions: const [MusicCloseButton()],
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(28),
          child: Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(
              room == null ? 'No room selected' : 'Plays in ${room.groupName ?? room.name}',
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
              key: const Key('podcast-search'),
              controller: _query,
              onChanged: _onChanged,
              onSubmitted: (v) => _search(v.trim()),
              textInputAction: TextInputAction.search,
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                hintText: 'Find a podcast',
                border: OutlineInputBorder(),
              ),
            ),
          ),
          if (_searching) const LinearProgressIndicator(),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Text(_error!, style: TextStyle(color: scheme.error)),
            ),
          Expanded(
            child: results != null
                ? ListView(
                    key: const Key('podcast-results'),
                    children: [
                      for (final p in results) _PodcastRow(podcast: p, onTap: () => _open(p)),
                      if (results.isEmpty && !_searching)
                        const Padding(
                          padding: EdgeInsets.all(24),
                          child: Text('No podcasts found.', textAlign: TextAlign.center),
                        ),
                    ],
                  )
                : subs == null
                ? const Center(child: CircularProgressIndicator())
                : subs.isEmpty
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(32),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(Icons.podcasts_rounded, size: 56, color: scheme.primary),
                          const SizedBox(height: 12),
                          Text('Find something to listen to', style: text.titleLarge),
                          const SizedBox(height: 8),
                          Text(
                            'Search above and subscribe. Your podcasts and where you stopped '
                            'are shared by every phone in the home.',
                            textAlign: TextAlign.center,
                            style: TextStyle(color: scheme.onSurfaceVariant),
                          ),
                        ],
                      ),
                    ),
                  )
                : CustomScrollView(
                    slivers: [
                      SliverToBoxAdapter(
                        child: Padding(
                          padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                          child: Text('Your podcasts', style: text.titleSmall),
                        ),
                      ),
                      SliverPadding(
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        sliver: SliverGrid.builder(
                          gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                            maxCrossAxisExtent: 150,
                            mainAxisSpacing: 12,
                            crossAxisSpacing: 12,
                            childAspectRatio: 0.78,
                          ),
                          itemCount: subs.length,
                          itemBuilder: (context, i) => _PodcastCover(podcast: subs[i], onTap: () => _open(subs[i])),
                        ),
                      ),
                      const SliverToBoxAdapter(
                        child: Padding(
                          padding: EdgeInsets.all(16),
                          child: Text('Directory search by Apple Podcasts.', textAlign: TextAlign.center),
                        ),
                      ),
                    ],
                  ),
          ),
        ],
      ),
    );
  }
}

class _PodcastRow extends StatelessWidget {
  const _PodcastRow({required this.podcast, required this.onTap});

  final Podcast podcast;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => ListTile(
    key: Key('podcast-${podcast.feed}'),
    leading: ClipRRect(
      borderRadius: BorderRadius.circular(8),
      child: SizedBox(
        width: 52,
        height: 52,
        child: Artwork(urls: [?podcast.image], icon: Icons.podcasts),
      ),
    ),
    title: Text(podcast.title, maxLines: 1, overflow: TextOverflow.ellipsis),
    subtitle: Text([?podcast.author, ?podcast.genre].join(' · '), maxLines: 1, overflow: TextOverflow.ellipsis),
    trailing: const Icon(Icons.chevron_right),
    onTap: onTap,
  );
}

class _PodcastCover extends StatelessWidget {
  const _PodcastCover({required this.podcast, required this.onTap});

  final Podcast podcast;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => InkWell(
    key: Key('podcast-sub-${podcast.feed}'),
    borderRadius: BorderRadius.circular(12),
    onTap: onTap,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AspectRatio(
          aspectRatio: 1,
          child: ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Artwork(urls: [?podcast.image], icon: Icons.podcasts),
          ),
        ),
        const SizedBox(height: 6),
        Text(podcast.title, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13)),
      ],
    ),
  );
}

/// One podcast: subscribe, and its episodes with how far you got.
class PodcastScreen extends ConsumerStatefulWidget {
  const PodcastScreen({super.key, required this.podcast});

  final Podcast podcast;

  @override
  ConsumerState<PodcastScreen> createState() => _PodcastScreenState();
}

class _PodcastScreenState extends ConsumerState<PodcastScreen> {
  PodcastFeed? _feed;
  String? _error;
  bool? _subscribed;
  String? _starting;

  PlaybackStore get _store => ref.read(playbackProvider.notifier);

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool fresh = false}) async {
    try {
      final feed = await _store.podcastFeed(widget.podcast, fresh: fresh);
      if (mounted) {
        setState(() {
          _feed = feed;
          _subscribed ??= feed.subscribed;
          _error = null;
        });
      }
    } on Object catch (e) {
      if (mounted) {
        setState(() => _error = userMessageFor(e));
      }
    }
  }

  Podcast get _podcast {
    final f = _feed?.podcast;
    final p = widget.podcast;
    return Podcast(feed: p.feed, title: f?.title ?? p.title, author: f?.author ?? p.author, image: f?.image ?? p.image);
  }

  Future<void> _toggleSubscribe() async {
    final want = !(_subscribed ?? false);
    setState(() => _subscribed = want);
    try {
      want ? await _store.subscribePodcast(_podcast) : await _store.unsubscribePodcast(_podcast.feed);
    } on Object catch (e) {
      if (mounted) {
        setState(() => _subscribed = !want);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(userMessageFor(e))));
      }
    }
  }

  Future<void> _play(PodcastEpisode ep, {bool fromStart = false}) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _starting = ep.id);
    try {
      await _store.playPodcast(ep, _podcast, fromStart: fromStart);
      final room = ref.read(playbackProvider).activeRoom;
      messenger.showSnackBar(
        SnackBar(content: Text('Playing ${ep.title} in ${room?.groupName ?? room?.name ?? 'the room'}')),
      );
    } on Object catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(userMessageFor(e))));
    } finally {
      if (mounted) {
        setState(() => _starting = null);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final feed = _feed;
    final p = _podcast;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final subscribed = _subscribed ?? false;
    return Scaffold(
      appBar: AppBar(
        title: Text(p.title, maxLines: 1, overflow: TextOverflow.ellipsis),
        actions: const [MusicCloseButton()],
      ),
      body: RefreshIndicator(
        onRefresh: () => _load(fresh: true),
        child: ListView(
          children: [
            Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 112,
                    height: 112,
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(14),
                      child: Artwork(urls: [?p.image], icon: Icons.podcasts),
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(p.title, style: text.titleLarge, maxLines: 3, overflow: TextOverflow.ellipsis),
                        if (p.author != null)
                          Text(p.author!, style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant)),
                        const SizedBox(height: 10),
                        subscribed
                            ? OutlinedButton.icon(
                                key: const Key('podcast-subscribe'),
                                onPressed: _toggleSubscribe,
                                icon: const Icon(Icons.check_rounded),
                                label: const Text('Subscribed'),
                              )
                            : FilledButton.icon(
                                key: const Key('podcast-subscribe'),
                                onPressed: _toggleSubscribe,
                                icon: const Icon(Icons.add_rounded),
                                label: const Text('Subscribe'),
                              ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            if (feed?.description != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                child: Text(
                  feed!.description!,
                  maxLines: 4,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: scheme.onSurfaceVariant),
                ),
              ),
            if (feed == null)
              Padding(
                padding: const EdgeInsets.all(24),
                child: Center(child: _error == null ? const CircularProgressIndicator() : Text(_error!)),
              )
            else
              for (final ep in feed.episodes)
                _EpisodeTile(
                  episode: ep,
                  busy: _starting == ep.id,
                  onPlay: () => _play(ep),
                  onFromStart: () => _play(ep, fromStart: true),
                ),
            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }
}

class _EpisodeTile extends StatelessWidget {
  const _EpisodeTile({required this.episode, required this.onPlay, required this.onFromStart, this.busy = false});

  final PodcastEpisode episode;
  final VoidCallback onPlay;
  final VoidCallback onFromStart;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final progress = episode.progress;
    final left = episode.canResume && episode.duration != null
        ? '${formatEpisodeLength(episode.duration! - episode.position!)} left'
        : formatEpisodeLength(episode.duration);
    final meta = [
      if (_ago(episode.date).isNotEmpty) _ago(episode.date),
      if (episode.done) 'Played' else if (left.isNotEmpty) left,
    ].join(' · ');
    return InkWell(
      key: Key('podcast-episode-${episode.id}'),
      onTap: busy ? null : onPlay,
      onLongPress: onFromStart,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 8, 10),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    episode.title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontWeight: FontWeight.w600, color: episode.done ? scheme.onSurfaceVariant : null),
                  ),
                  const SizedBox(height: 4),
                  Text(meta, style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 12.5)),
                  if (progress != null && !episode.done) ...[
                    const SizedBox(height: 6),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(4),
                      child: LinearProgressIndicator(
                        key: Key('podcast-progress-${episode.id}'),
                        value: progress,
                        minHeight: 4,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(width: 8),
            busy
                ? const Padding(
                    padding: EdgeInsets.all(12),
                    child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2)),
                  )
                : IconButton(
                    tooltip: episode.canResume ? 'Resume' : 'Play',
                    iconSize: 34,
                    color: scheme.primary,
                    icon: Icon(episode.canResume ? Icons.play_circle_outline_rounded : Icons.play_circle_fill_rounded),
                    onPressed: onPlay,
                  ),
            PopupMenuButton<String>(
              tooltip: 'More',
              onSelected: (_) => onFromStart(),
              itemBuilder: (_) => const [PopupMenuItem(value: 'start', child: Text('Play from the start'))],
            ),
          ],
        ),
      ),
    );
  }
}
