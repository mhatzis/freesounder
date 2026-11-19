import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/errors.dart';
import '../models/library.dart';
import '../state/playback_store.dart';
import 'artwork.dart';
import 'music_nav.dart';

Future<void> showLibraryScreen(BuildContext context) {
  return pushMusic<void>(context, 'library', (_) => const LibraryScreen());
}

String _targetName(WidgetRef ref) {
  final room = ref.read(playbackProvider).activeRoom;
  return room == null ? 'the room' : (room.isGrouped ? (room.groupName ?? room.name) : room.name);
}

Future<void> _playTracks(
  BuildContext context,
  WidgetRef ref,
  List<LibraryTrack> tracks, {
  int start = 0,
  String? contextName,
}) async {
  if (tracks.isEmpty) {
    return;
  }
  final messenger = ScaffoldMessenger.of(context);
  try {
    await ref.read(playbackProvider.notifier).playLibrary(tracks, start: start, contextName: contextName);
    messenger.showSnackBar(SnackBar(content: Text('Playing ${tracks[start].title} in ${_targetName(ref)}')));
  } on Object catch (err) {
    messenger.showSnackBar(SnackBar(content: Text(userMessageFor(err))));
  }
}

String _length(double? seconds) {
  if (seconds == null || seconds <= 0) {
    return '';
  }
  final s = seconds.round();
  return '${s ~/ 60}:${(s % 60).toString().padLeft(2, '0')}';
}

/// The home's music library: NAS shares (SMB / NFS) and USB drives, indexed
/// on one player and playable in any room or group.
class LibraryScreen extends ConsumerStatefulWidget {
  const LibraryScreen({super.key});

  @override
  ConsumerState<LibraryScreen> createState() => _LibraryScreenState();
}

enum _View { albums, artists, folders }

class _LibraryScreenState extends ConsumerState<LibraryScreen> {
  final _query = TextEditingController();
  Timer? _debounce;
  LibrarySummary? _summary;
  String? _error;
  bool _loading = true;
  _View _view = _View.albums;
  List<LibraryAlbum> _albums = const [];
  List<String> _artists = const [];
  LibraryListing _root = const LibraryListing();
  LibrarySearchResult? _results;

  PlaybackStore get _store => ref.read(playbackProvider.notifier);

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _query.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final summary = await _store.librarySummary();
      List<LibraryAlbum> albums = const [];
      List<String> artists = const [];
      var root = const LibraryListing();
      if (summary.shares.isNotEmpty) {
        (albums, artists, root) = await (
          _store.libraryAlbums(),
          _store.libraryArtists(),
          _store.libraryFolder(''),
        ).wait;
      }
      if (mounted) {
        setState(() {
          _summary = summary;
          _albums = albums;
          _artists = artists;
          _root = root;
        });
      }
    } on Object catch (err) {
      if (mounted) {
        setState(() => _error = err is ParallelWaitError ? userMessageFor(err.errors) : userMessageFor(err));
      }
    } finally {
      if (mounted) {
        setState(() => _loading = false);
      }
    }
  }

  void _onSearch(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), () async {
      final q = value.trim();
      if (q.isEmpty) {
        setState(() => _results = null);
        return;
      }
      try {
        final results = await _store.librarySearch(q);
        if (mounted && _query.text.trim() == q) {
          setState(() => _results = results);
        }
      } on Object catch (err) {
        if (mounted) {
          setState(() => _error = userMessageFor(err));
        }
      }
    });
  }

  Future<void> _manageShares() async {
    await pushMusic<void>(context, 'library/shares', (_) => const LibrarySharesScreen());
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final room = ref.watch(playbackProvider.select((s) => s.activeRoom));
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final summary = _summary;
    final empty = summary != null && summary.shares.isEmpty;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Music library'),
        actions: [
          IconButton(
            key: const Key('library-shares'),
            tooltip: 'Music shares',
            icon: const Icon(Icons.dns_outlined),
            onPressed: _manageShares,
          ),
          const MusicCloseButton(),
        ],
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
      body: _loading && summary == null
          ? const Center(child: CircularProgressIndicator())
          : _error != null && summary == null
          ? _Message(icon: Icons.cloud_off_rounded, title: 'Library not available', body: _error!, onRetry: _load)
          : empty
          ? _Message(
              key: const Key('library-empty'),
              icon: Icons.album_rounded,
              title: 'Add your music',
              body:
                  'Point FreeSounder at a NAS share (SMB or NFS) or a USB drive plugged into a player. '
                  'One player indexes it; every room can play it.',
              action: FilledButton.icon(
                key: const Key('library-add-first'),
                onPressed: _manageShares,
                icon: const Icon(Icons.add),
                label: const Text('Add music share'),
              ),
            )
          : RefreshIndicator(onRefresh: _load, child: _content(summary!)),
    );
  }

  Widget _content(LibrarySummary summary) {
    final scheme = Theme.of(context).colorScheme;
    final results = _results;
    return CustomScrollView(
      slivers: [
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
            child: TextField(
              key: const Key('library-search'),
              controller: _query,
              onChanged: _onSearch,
              textInputAction: TextInputAction.search,
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                hintText: 'Search artists, albums, songs',
                border: OutlineInputBorder(),
              ),
            ),
          ),
        ),
        if (summary.scanning || summary.shares.any((s) => s.error != null))
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
              child: Text(
                summary.scanning
                    ? 'Indexing… new music appears as it is found.'
                    : 'A share could not be reached. Check it in Music shares.',
                style: TextStyle(color: summary.scanning ? scheme.onSurfaceVariant : scheme.error),
              ),
            ),
          ),
        if (results != null)
          ..._searchSlivers(results)
        else ...[
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
              child: SegmentedButton<_View>(
                key: const Key('library-view'),
                segments: const [
                  ButtonSegment(value: _View.albums, label: Text('Albums'), icon: Icon(Icons.album_outlined)),
                  ButtonSegment(value: _View.artists, label: Text('Artists'), icon: Icon(Icons.person_outline)),
                  ButtonSegment(value: _View.folders, label: Text('Folders'), icon: Icon(Icons.folder_outlined)),
                ],
                selected: {_view},
                showSelectedIcon: false,
                onSelectionChanged: (v) => setState(() => _view = v.first),
              ),
            ),
          ),
          ...switch (_view) {
            _View.albums => [AlbumGrid(albums: _albums)],
            _View.artists => [
              SliverList.builder(
                itemCount: _artists.length,
                itemBuilder: (context, i) => _ArtistTile(artist: _artists[i]),
              ),
            ],
            _View.folders => [SliverList.list(children: _folderTiles(context, _root))],
          },
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                '${summary.songs} songs · ${summary.albums} albums'
                '${summary.hostRoom == null ? '' : ' · indexed on ${summary.hostRoom}'}',
                textAlign: TextAlign.center,
                style: TextStyle(color: scheme.onSurfaceVariant),
              ),
            ),
          ),
        ],
      ],
    );
  }

  List<Widget> _searchSlivers(LibrarySearchResult r) {
    if (r.isEmpty) {
      return const [
        SliverToBoxAdapter(
          child: Padding(
            padding: EdgeInsets.all(24),
            child: Text('Nothing found.', textAlign: TextAlign.center),
          ),
        ),
      ];
    }
    return [
      if (r.artists.isNotEmpty) ...[
        const _Header('Artists'),
        SliverList.list(children: [for (final a in r.artists) _ArtistTile(artist: a)]),
      ],
      if (r.albums.isNotEmpty) ...[const _Header('Albums'), AlbumGrid(albums: r.albums)],
      if (r.tracks.isNotEmpty) ...[
        const _Header('Songs'),
        SliverList.builder(
          itemCount: r.tracks.length,
          itemBuilder: (context, i) =>
              TrackTile(track: r.tracks[i], showAlbum: true, onTap: () => _playTracks(context, ref, [r.tracks[i]])),
        ),
      ],
    ];
  }
}

List<Widget> _folderTiles(BuildContext context, LibraryListing listing) => [
  for (final f in listing.folders)
    ListTile(
      key: Key('library-folder-${f.path}'),
      leading: const Icon(Icons.folder_rounded),
      title: Text(f.name, maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: const Icon(Icons.chevron_right),
      onTap: () => pushMusic<void>(context, 'library/folder', (_) => FolderScreen(path: f.path, title: f.name)),
    ),
];

class _Header extends StatelessWidget {
  const _Header(this.title);

  final String title;

  @override
  Widget build(BuildContext context) => SliverToBoxAdapter(
    child: Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 6),
      child: Text(title, style: Theme.of(context).textTheme.titleSmall),
    ),
  );
}

class _Message extends StatelessWidget {
  const _Message({super.key, required this.icon, required this.title, required this.body, this.action, this.onRetry});

  final IconData icon;
  final String title;
  final String body;
  final Widget? action;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 56, color: scheme.primary),
            const SizedBox(height: 12),
            Text(title, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            Text(
              body,
              textAlign: TextAlign.center,
              style: TextStyle(color: scheme.onSurfaceVariant),
            ),
            const SizedBox(height: 20),
            ?action,
            if (onRetry != null) OutlinedButton(onPressed: onRetry, child: const Text('Try again')),
          ],
        ),
      ),
    );
  }
}

class _ArtistTile extends ConsumerWidget {
  const _ArtistTile({required this.artist});

  final String artist;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return ListTile(
      key: Key('library-artist-$artist'),
      leading: const CircleAvatar(child: Icon(Icons.person_rounded)),
      title: Text(artist, maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: const Icon(Icons.chevron_right),
      onTap: () => pushMusic<void>(context, 'library/artist', (_) => ArtistScreen(artist: artist)),
    );
  }
}

/// Album covers, two or more per row.
class AlbumGrid extends ConsumerWidget {
  const AlbumGrid({super.key, required this.albums});

  final List<LibraryAlbum> albums;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final store = ref.read(playbackProvider.notifier);
    return SliverPadding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      sliver: SliverGrid.builder(
        gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
          maxCrossAxisExtent: 190,
          mainAxisSpacing: 14,
          crossAxisSpacing: 14,
          childAspectRatio: 0.78,
        ),
        itemCount: albums.length,
        itemBuilder: (context, i) {
          final a = albums[i];
          final art = store.libraryArtUrl(album: a.album, artist: a.artist);
          return InkWell(
            key: Key('library-album-${a.album}'),
            borderRadius: BorderRadius.circular(12),
            onTap: () => pushMusic<void>(context, 'library/album', (_) => AlbumScreen(album: a)),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                AspectRatio(
                  aspectRatio: 1,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(12),
                    child: Artwork(urls: [?art], icon: Icons.album_rounded),
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  a.album,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                if (a.artist != null)
                  Text(
                    a.artist!,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant, fontSize: 12.5),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class TrackTile extends StatelessWidget {
  const TrackTile({super.key, required this.track, required this.onTap, this.number, this.showAlbum = false});

  final LibraryTrack track;
  final VoidCallback onTap;
  final int? number;
  final bool showAlbum;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final sub = [?track.artist, if (showAlbum && track.album != null) track.album!].join(' · ');
    return ListTile(
      key: Key('library-track-${track.uri}'),
      leading: number == null
          ? const Icon(Icons.music_note_rounded)
          : SizedBox(
              width: 28,
              child: Text(
                '$number',
                textAlign: TextAlign.center,
                style: TextStyle(color: scheme.onSurfaceVariant),
              ),
            ),
      title: Text(track.title, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: sub.isEmpty ? null : Text(sub, maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: Text(_length(track.duration), style: TextStyle(color: scheme.onSurfaceVariant)),
      onTap: onTap,
    );
  }
}

/// Cover, title and Play, then the tracks: tap one to play from there.
class _TrackListScreen extends ConsumerStatefulWidget {
  const _TrackListScreen({required this.title, this.subtitle, this.art, required this.load, this.numbered = true});

  final String title;
  final String? subtitle;
  final String? art;
  final Future<LibraryListing> Function() load;
  final bool numbered;

  @override
  ConsumerState<_TrackListScreen> createState() => _TrackListScreenState();
}

class _TrackListScreenState extends ConsumerState<_TrackListScreen> {
  LibraryListing? _listing;
  String? _error;

  @override
  void initState() {
    super.initState();
    widget.load().then(
      (l) => mounted ? setState(() => _listing = l) : null,
      onError: (Object e) => mounted ? setState(() => _error = userMessageFor(e)) : null,
    );
  }

  @override
  Widget build(BuildContext context) {
    final listing = _listing;
    final text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.title, maxLines: 1, overflow: TextOverflow.ellipsis),
        actions: const [MusicCloseButton()],
      ),
      body: listing == null
          ? Center(child: _error == null ? const CircularProgressIndicator() : Text(_error!))
          : ListView(
              children: [
                if (widget.art != null || listing.tracks.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.all(16),
                    child: Row(
                      children: [
                        SizedBox(
                          width: 112,
                          height: 112,
                          child: ClipRRect(
                            borderRadius: BorderRadius.circular(14),
                            child: Artwork(urls: [?widget.art], icon: Icons.album_rounded),
                          ),
                        ),
                        const SizedBox(width: 16),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(widget.title, style: text.titleLarge, maxLines: 2, overflow: TextOverflow.ellipsis),
                              if (widget.subtitle != null) Text(widget.subtitle!, style: text.bodyMedium),
                              const SizedBox(height: 10),
                              if (listing.tracks.isNotEmpty)
                                FilledButton.icon(
                                  key: const Key('library-play-all'),
                                  onPressed: () => _playTracks(context, ref, listing.tracks, contextName: widget.title),
                                  icon: const Icon(Icons.play_arrow_rounded),
                                  label: const Text('Play'),
                                ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ..._folderTiles(context, listing),
                for (final (i, t) in listing.tracks.indexed)
                  TrackTile(
                    track: t,
                    number: widget.numbered ? i + 1 : null,
                    onTap: () => _playTracks(context, ref, listing.tracks, start: i, contextName: widget.title),
                  ),
                const SizedBox(height: 24),
              ],
            ),
    );
  }
}

class AlbumScreen extends ConsumerWidget {
  const AlbumScreen({super.key, required this.album});

  final LibraryAlbum album;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final store = ref.read(playbackProvider.notifier);
    return _TrackListScreen(
      title: album.album,
      subtitle: album.artist,
      art: store.libraryArtUrl(album: album.album, artist: album.artist),
      load: () async => LibraryListing(tracks: await store.libraryAlbum(album.album, artist: album.artist)),
    );
  }
}

class FolderScreen extends ConsumerWidget {
  const FolderScreen({super.key, required this.path, required this.title});

  final String path;
  final String title;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final store = ref.read(playbackProvider.notifier);
    return _TrackListScreen(
      title: title,
      load: () async {
        final listing = await store.libraryFolder(path);
        return listing;
      },
      art: null,
      numbered: false,
    );
  }
}

class ArtistScreen extends ConsumerStatefulWidget {
  const ArtistScreen({super.key, required this.artist});

  final String artist;

  @override
  ConsumerState<ArtistScreen> createState() => _ArtistScreenState();
}

class _ArtistScreenState extends ConsumerState<ArtistScreen> {
  List<LibraryAlbum>? _albums;
  String? _error;

  @override
  void initState() {
    super.initState();
    ref
        .read(playbackProvider.notifier)
        .libraryAlbums(artist: widget.artist)
        .then(
          (a) => mounted ? setState(() => _albums = a) : null,
          onError: (Object e) => mounted ? setState(() => _error = userMessageFor(e)) : null,
        );
  }

  @override
  Widget build(BuildContext context) {
    final albums = _albums;
    return Scaffold(
      appBar: AppBar(title: Text(widget.artist), actions: const [MusicCloseButton()]),
      body: albums == null
          ? Center(child: _error == null ? const CircularProgressIndicator() : Text(_error!))
          : CustomScrollView(
              slivers: [
                const SliverToBoxAdapter(child: SizedBox(height: 12)),
                AlbumGrid(albums: albums),
                const SliverToBoxAdapter(child: SizedBox(height: 24)),
              ],
            ),
    );
  }
}

// ---- shares ---------------------------------------------------------------------

/// Network shares and USB drives the library is built from.
class LibrarySharesScreen extends ConsumerStatefulWidget {
  const LibrarySharesScreen({super.key});

  @override
  ConsumerState<LibrarySharesScreen> createState() => _LibrarySharesScreenState();
}

class _LibrarySharesScreenState extends ConsumerState<LibrarySharesScreen> {
  LibrarySummary? _summary;
  String? _error;

  PlaybackStore get _store => ref.read(playbackProvider.notifier);

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final s = await _store.librarySummary();
      if (mounted) {
        setState(() {
          _summary = s;
          _error = null;
        });
      }
    } on Object catch (e) {
      if (mounted) {
        setState(() => _error = userMessageFor(e));
      }
    }
  }

  Future<void> _remove(LibraryShare share) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Remove ${share.name}?'),
        content: const Text('Its music leaves the library. Nothing on the share is changed.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Remove')),
        ],
      ),
    );
    if (ok != true) {
      return;
    }
    try {
      await _store.removeLibraryShare(share.id);
    } on Object catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(userMessageFor(e))));
      }
    }
    await _load();
  }

  Future<void> _rescan() async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await _store.rescanLibrary();
      messenger.showSnackBar(const SnackBar(content: Text('Looking for new music…')));
    } on Object catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(userMessageFor(e))));
    }
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final summary = _summary;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Music shares'),
        actions: [
          if (summary != null && summary.shares.isNotEmpty)
            IconButton(tooltip: 'Rescan', icon: const Icon(Icons.refresh_rounded), onPressed: _rescan),
          const MusicCloseButton(),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        key: const Key('library-add-share'),
        onPressed: () async {
          final added = await pushMusic<bool>(context, 'library/add-share', (_) => const AddShareScreen());
          if (added == true) {
            await _load();
          }
        },
        icon: const Icon(Icons.add),
        label: const Text('Add'),
      ),
      body: summary == null
          ? Center(child: _error == null ? const CircularProgressIndicator() : Text(_error!))
          : ListView(
              children: [
                for (final s in summary.shares)
                  ListTile(
                    key: Key('library-share-${s.id}'),
                    leading: Icon(s.kind == 'usb' ? Icons.usb_rounded : Icons.dns_rounded),
                    title: Text(s.name),
                    subtitle: Text(
                      s.error ?? (s.mounted ? s.address : '${s.address} · not connected'),
                      style: TextStyle(color: s.error != null ? scheme.error : null),
                    ),
                    trailing: IconButton(
                      tooltip: 'Remove',
                      icon: const Icon(Icons.delete_outline),
                      onPressed: () => _remove(s),
                    ),
                  ),
                if (summary.shares.isEmpty)
                  const Padding(padding: EdgeInsets.all(24), child: Text('No music shares yet.')),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
                  child: Text(
                    'Shares are mounted read-only. Passwords stay on the player and are never shown again.'
                    '${summary.hostRoom == null ? '' : ' The library is indexed on ${summary.hostRoom}.'}',
                    style: TextStyle(color: scheme.onSurfaceVariant),
                  ),
                ),
              ],
            ),
    );
  }
}

class AddShareScreen extends ConsumerStatefulWidget {
  const AddShareScreen({super.key});

  @override
  ConsumerState<AddShareScreen> createState() => _AddShareScreenState();
}

class _AddShareScreenState extends ConsumerState<AddShareScreen> {
  String _kind = 'smb';
  final _host = TextEditingController();
  final _share = TextEditingController();
  final _user = TextEditingController();
  final _password = TextEditingController();
  final _name = TextEditingController();
  bool _busy = false;
  bool _showPassword = false;
  String? _error;
  List<UsbDrive>? _drives;
  String? _drive;

  @override
  void dispose() {
    for (final c in [_host, _share, _user, _password, _name]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _loadDrives() async {
    try {
      final d = await ref.read(playbackProvider.notifier).usbDrives();
      if (mounted) {
        setState(() {
          _drives = d;
          _drive = d.isEmpty ? null : d.first.uuid;
        });
      }
    } on Object catch (e) {
      if (mounted) {
        setState(() => _error = userMessageFor(e));
      }
    }
  }

  Future<void> _save() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final body = <String, dynamic>{
      'kind': _kind,
      if (_name.text.trim().isNotEmpty) 'name': _name.text.trim(),
      if (_kind != 'usb') ...{'host': _host.text.trim(), 'share': _share.text.trim()},
      if (_kind == 'smb' && _user.text.trim().isNotEmpty) ...{
        'username': _user.text.trim(),
        'password': _password.text,
      },
      if (_kind == 'usb') 'uuid': ?_drive,
    };
    try {
      await ref.read(playbackProvider.notifier).addLibraryShare(body);
      _password.clear();
      if (mounted) {
        Navigator.of(context).pop(true);
      }
    } on Object catch (e) {
      if (mounted) {
        setState(() => _error = userMessageFor(e));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  bool get _valid => switch (_kind) {
    'usb' => _drive != null,
    _ => _host.text.trim().isNotEmpty && _share.text.trim().isNotEmpty,
  };

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Add music'), actions: const [MusicCloseButton()]),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          SegmentedButton<String>(
            key: const Key('share-kind'),
            segments: const [
              ButtonSegment(value: 'smb', label: Text('Windows / SMB')),
              ButtonSegment(value: 'nfs', label: Text('NFS')),
              ButtonSegment(value: 'usb', label: Text('USB')),
            ],
            selected: {_kind},
            showSelectedIcon: false,
            onSelectionChanged: (v) {
              setState(() => _kind = v.first);
              if (_kind == 'usb' && _drives == null) {
                _loadDrives();
              }
            },
          ),
          const SizedBox(height: 16),
          if (_kind == 'usb') ...[
            if (_drives == null)
              const Center(child: CircularProgressIndicator())
            else if (_drives!.isEmpty)
              const Text('No USB drive found. Plug one into the player that holds the library and try again.')
            else
              RadioGroup<String>(
                groupValue: _drive,
                onChanged: (v) => setState(() => _drive = v),
                child: Column(
                  children: [
                    for (final d in _drives!)
                      RadioListTile<String>(
                        value: d.uuid,
                        title: Text(d.name),
                        subtitle: d.size == null ? null : Text(d.size!),
                      ),
                  ],
                ),
              ),
          ] else ...[
            TextField(
              key: const Key('share-host'),
              controller: _host,
              onChanged: (_) => setState(() {}),
              keyboardType: TextInputType.url,
              autocorrect: false,
              decoration: const InputDecoration(labelText: 'NAS address', hintText: 'nas.local or 192.168.1.20'),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('share-path'),
              controller: _share,
              onChanged: (_) => setState(() {}),
              autocorrect: false,
              decoration: InputDecoration(
                labelText: _kind == 'smb' ? 'Share name' : 'Export path',
                hintText: _kind == 'smb' ? 'music  or  media/Music' : '/volume1/music',
              ),
            ),
            if (_kind == 'smb') ...[
              const SizedBox(height: 12),
              TextField(
                key: const Key('share-user'),
                controller: _user,
                autocorrect: false,
                decoration: const InputDecoration(labelText: 'User name (optional)', hintText: 'Leave empty for guest'),
              ),
              const SizedBox(height: 12),
              TextField(
                key: const Key('share-password'),
                controller: _password,
                obscureText: !_showPassword,
                autocorrect: false,
                enableSuggestions: false,
                decoration: InputDecoration(
                  labelText: 'Password (optional)',
                  suffixIcon: IconButton(
                    icon: Icon(_showPassword ? Icons.visibility_off : Icons.visibility),
                    onPressed: () => setState(() => _showPassword = !_showPassword),
                  ),
                ),
              ),
            ],
          ],
          const SizedBox(height: 12),
          TextField(
            key: const Key('share-name'),
            controller: _name,
            decoration: const InputDecoration(labelText: 'Name in the app (optional)', hintText: 'Music'),
          ),
          const SizedBox(height: 20),
          if (_error != null) ...[
            Text(
              _error!,
              key: const Key('share-error'),
              style: TextStyle(color: scheme.error),
            ),
            const SizedBox(height: 12),
          ],
          FilledButton(
            key: const Key('share-save'),
            onPressed: _busy || !_valid ? null : _save,
            child: _busy
                ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                : const Text('Connect and add'),
          ),
          const SizedBox(height: 12),
          Text(
            'Mounted read-only. The password is stored only on the player and never shown or logged.',
            style: TextStyle(color: scheme.onSurfaceVariant),
          ),
        ],
      ),
    );
  }
}
