import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/errors.dart';
import '../../models/room.dart';
import '../../state/playback_store.dart';
import '../../ui/artwork.dart';
import '../spotify_api.dart';
import '../spotify_auth.dart';
import '../spotify_models.dart';
import '../spotify_store.dart';
import 'spotify_setup.dart';

Future<void> showSpotifyScreen(BuildContext context) {
  return Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => const SpotifyScreen()));
}

String spotifyErrorText(Object err) {
  if (err is SpotifyApiException) {
    return err.friendly;
  }
  if (err is SpotifyAuthException) {
    return err.message;
  }
  return userMessageFor(err);
}

/// Spotify: setup until connected, then home shelves and search.
class SpotifyScreen extends ConsumerWidget {
  const SpotifyScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final spotify = ref.watch(spotifyProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Spotify'),
        actions: [
          if (spotify.signedIn)
            PopupMenuButton<String>(
              key: const Key('spotify-account-menu'),
              onSelected: (value) {
                if (value == 'signout') {
                  ref.read(spotifyProvider.notifier).signOut();
                }
              },
              itemBuilder: (_) => [
                PopupMenuItem(
                  enabled: false,
                  child: Text(spotify.displayName?.isNotEmpty == true ? 'Signed in as ${spotify.displayName}' : 'Signed in'),
                ),
                const PopupMenuItem(value: 'signout', child: Text('Sign out of Spotify')),
              ],
            ),
        ],
      ),
      body: !spotify.loaded
          ? const Center(child: CircularProgressIndicator())
          : spotify.signedIn
              ? const _Browse()
              : const SpotifySetup(),
    );
  }
}

class _Browse extends ConsumerStatefulWidget {
  const _Browse();

  @override
  ConsumerState<_Browse> createState() => _BrowseState();
}

class _BrowseState extends ConsumerState<_Browse> {
  final _query = TextEditingController();
  Future<List<SpotifySection>>? _home;
  Future<List<SpotifySection>>? _results;
  Timer? _debounce;

  @override
  void initState() {
    super.initState();
    _home = ref.read(spotifyProvider.notifier).home();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _query.dispose();
    super.dispose();
  }

  void _search(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), () {
      setState(() {
        _results = value.trim().isEmpty ? null : ref.read(spotifyProvider.notifier).search(value.trim());
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    final future = _results ?? _home;
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
          child: TextField(
            key: const Key('spotify-search'),
            controller: _query,
            textInputAction: TextInputAction.search,
            decoration: InputDecoration(
              prefixIcon: const Icon(Icons.search),
              hintText: 'Songs, artists, albums, playlists',
              border: const OutlineInputBorder(),
              suffixIcon: _query.text.isEmpty
                  ? null
                  : IconButton(
                      icon: const Icon(Icons.clear),
                      onPressed: () {
                        _query.clear();
                        _search('');
                      },
                    ),
            ),
            onChanged: _search,
            onSubmitted: _search,
          ),
        ),
        Expanded(
          child: FutureBuilder<List<SpotifySection>>(
            future: future,
            builder: (context, snap) {
              if (snap.connectionState != ConnectionState.done) {
                return const Center(child: CircularProgressIndicator());
              }
              if (snap.hasError) {
                return _ErrorRetry(
                  message: spotifyErrorText(snap.error!),
                  onRetry: () => setState(() {
                    _home = ref.read(spotifyProvider.notifier).home();
                    if (_query.text.isNotEmpty) {
                      _results = ref.read(spotifyProvider.notifier).search(_query.text);
                    }
                  }),
                );
              }
              final sections = snap.data ?? const [];
              if (sections.isEmpty) {
                return Center(child: Text(_results != null ? 'Nothing found' : 'Nothing in your library yet'));
              }
              return RefreshIndicator(
                onRefresh: () async {
                  final next = ref.read(spotifyProvider.notifier).home();
                  setState(() => _home = next);
                  await next;
                },
                child: ListView(
                  key: const Key('spotify-sections'),
                  padding: const EdgeInsets.only(bottom: 24),
                  children: [for (final s in sections) SpotifyShelf(section: s, asList: _results != null && s.title == 'Songs')],
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}

class _ErrorRetry extends StatelessWidget {
  const _ErrorRetry({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(message, textAlign: TextAlign.center, key: const Key('spotify-load-error')),
            const SizedBox(height: 12),
            OutlinedButton(onPressed: onRetry, child: const Text('Try again')),
          ],
        ),
      ),
    );
  }
}

/// A titled row of cards, or a list for songs.
class SpotifyShelf extends StatelessWidget {
  const SpotifyShelf({super.key, required this.section, this.asList = false});

  final SpotifySection section;
  final bool asList;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final songs = section.items.every((i) => i.type == 'track' || i.type == 'episode');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
          child: Text(section.title, style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
        ),
        if (asList || (songs && section.items.length <= 6))
          for (final item in section.items) SpotifyTrackTile(item: item)
        else
          SizedBox(
            height: 196,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 16),
              itemCount: section.items.length,
              separatorBuilder: (_, _) => const SizedBox(width: 12),
              itemBuilder: (context, i) => _Card(item: section.items[i]),
            ),
          ),
      ],
    );
  }
}

class _Card extends StatelessWidget {
  const _Card({required this.item});

  final SpotifyItem item;

  @override
  Widget build(BuildContext context) {
    final round = item.type == 'artist';
    final text = Theme.of(context).textTheme;
    return SizedBox(
      width: 136,
      child: InkWell(
        key: Key('spotify-card-${item.id}'),
        borderRadius: BorderRadius.circular(12),
        onTap: () => openSpotifyItem(context, item),
        onLongPress: () => playSpotifyItem(context, item),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 136,
              height: 136,
              child: ClipRRect(
                borderRadius: BorderRadius.circular(round ? 68 : 10),
                child: Artwork(urls: [?item.imageUrl]),
              ),
            ),
            const SizedBox(height: 6),
            Text(item.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: text.bodyMedium),
            if (item.subtitle != null)
              Text(item.subtitle!, maxLines: 1, overflow: TextOverflow.ellipsis, style: text.bodySmall),
          ],
        ),
      ),
    );
  }
}

class SpotifyTrackTile extends StatelessWidget {
  const SpotifyTrackTile({super.key, required this.item, this.number});

  final SpotifyItem item;
  final int? number;

  @override
  Widget build(BuildContext context) {
    final duration = item.durationMs == null ? null : formatDuration(item.durationMs! / 1000);
    return ListTile(
      key: Key('spotify-track-${item.id}'),
      leading: number != null
          ? SizedBox(width: 32, child: Center(child: Text('$number')))
          : SizedBox(
              width: 44,
              height: 44,
              child: ClipRRect(borderRadius: BorderRadius.circular(6), child: Artwork(urls: [?item.imageUrl])),
            ),
      title: Text(item.name, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: item.subtitle == null ? null : Text(item.subtitle!, maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: duration == null ? null : Text(duration),
      onTap: item.type == 'track' || item.type == 'episode' ? () => playSpotifyItem(context, item) : () => openSpotifyItem(context, item),
    );
  }
}

void openSpotifyItem(BuildContext context, SpotifyItem item) {
  if (item.type == 'track' || item.type == 'episode') {
    playSpotifyItem(context, item);
    return;
  }
  Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => SpotifyPageScreen(item: item)));
}

/// Pick a room or group, then play. Groups appear once ("Kitchen + 1").
Future<void> playSpotifyItem(BuildContext context, SpotifyItem item) async {
  final container = ProviderScope.containerOf(context);
  final playback = container.read(playbackProvider);
  final targets = playTargets(playback.rooms);
  if (targets.isEmpty) {
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Add a player first')));
    return;
  }
  final active = playback.activeRoom;
  final chosen = targets.length == 1
      ? targets.first
      : await showModalBottomSheet<PlayTarget>(
          context: context,
          showDragHandle: true,
          builder: (sheet) => SafeArea(
            child: ListView(
              shrinkWrap: true,
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
                  child: Text('Play "${item.name}" on', style: Theme.of(sheet).textTheme.titleMedium),
                ),
                for (final t in targets)
                  ListTile(
                    key: Key('spotify-target-${t.id}'),
                    leading: Icon(t.grouped ? Icons.speaker_group : Icons.speaker),
                    title: Text(t.name),
                    trailing: active != null && t.memberIds.contains(active.id) ? const Icon(Icons.check) : null,
                    onTap: () => Navigator.of(sheet).pop(t),
                  ),
              ],
            ),
          ),
        );
  if (chosen == null || !context.mounted) {
    return;
  }
  final messenger = ScaffoldMessenger.of(context);
  messenger.showSnackBar(SnackBar(content: Text('Starting on ${chosen.name}…'), duration: const Duration(seconds: 20)));
  try {
    await container.read(spotifyProvider.notifier).play(chosen.id, item);
    messenger.hideCurrentSnackBar();
    messenger.showSnackBar(SnackBar(content: Text('Playing on ${chosen.name}')));
    container.read(playbackProvider.notifier).selectRoom(chosen.memberIds.first);
  } on Object catch (err) {
    messenger.hideCurrentSnackBar();
    messenger.showSnackBar(SnackBar(content: Text(spotifyErrorText(err)), duration: const Duration(seconds: 8)));
  }
}

class PlayTarget {
  const PlayTarget({required this.id, required this.name, required this.memberIds});

  final String id;
  final String name;
  final List<String> memberIds;

  bool get grouped => memberIds.length > 1;
}

/// One entry per group (its first room's id works as the group id).
List<PlayTarget> playTargets(List<Room> rooms) {
  final seen = <String>{};
  final out = <PlayTarget>[];
  for (final room in rooms) {
    if (!room.available) {
      continue;
    }
    final key = room.isGrouped ? (room.groupId ?? room.id) : room.id;
    if (!seen.add(key)) {
      continue;
    }
    out.add(PlayTarget(
      id: room.isGrouped ? (room.groupId ?? room.id) : room.id,
      name: room.isGrouped ? (room.groupName ?? room.name) : room.name,
      memberIds: room.isGrouped ? room.memberIds : [room.id],
    ));
  }
  return out;
}

/// Album, playlist, artist or podcast page.
class SpotifyPageScreen extends ConsumerStatefulWidget {
  const SpotifyPageScreen({super.key, required this.item});

  final SpotifyItem item;

  @override
  ConsumerState<SpotifyPageScreen> createState() => _SpotifyPageScreenState();
}

class _SpotifyPageScreenState extends ConsumerState<SpotifyPageScreen> {
  late Future<SpotifyPage> _page = ref.read(spotifyProvider.notifier).page(widget.item);

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: Text(widget.item.name, maxLines: 1, overflow: TextOverflow.ellipsis)),
      body: FutureBuilder<SpotifyPage>(
        future: _page,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            return _ErrorRetry(
              message: spotifyErrorText(snap.error!),
              onRetry: () => setState(() => _page = ref.read(spotifyProvider.notifier).page(widget.item)),
            );
          }
          final page = snap.data!;
          final numbered = page.item.type == 'album';
          return ListView(
            key: const Key('spotify-page'),
            padding: const EdgeInsets.only(bottom: 32),
            children: [
              const SizedBox(height: 12),
              Center(
                child: SizedBox(
                  width: 220,
                  height: 220,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(page.item.type == 'artist' ? 110 : 14),
                    child: Artwork(urls: [?page.imageUrl, ?page.item.imageUrl]),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                child: Column(
                  children: [
                    Text(page.item.name, textAlign: TextAlign.center, style: text.titleLarge?.copyWith(fontWeight: FontWeight.w600)),
                    if (page.item.subtitle != null) Text(page.item.subtitle!, textAlign: TextAlign.center),
                  ],
                ),
              ),
              const SizedBox(height: 12),
              Center(
                child: FilledButton.icon(
                  key: const Key('spotify-play-page'),
                  onPressed: () => playSpotifyItem(context, page.item),
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('Play'),
                ),
              ),
              if (page.note != null)
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 12, 20, 0),
                  child: Text(page.note!, textAlign: TextAlign.center, style: text.bodySmall),
                ),
              for (final s in page.sections) SpotifyShelf(section: s),
              const SizedBox(height: 8),
              for (var i = 0; i < page.tracks.length; i++)
                SpotifyTrackTile(item: page.tracks[i], number: numbered ? i + 1 : null),
            ],
          );
        },
      ),
    );
  }
}
