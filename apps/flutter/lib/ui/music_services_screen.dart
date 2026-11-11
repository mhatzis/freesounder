import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/control_client.dart';
import '../models/music_service.dart';
import '../state/playback_store.dart';
import 'music_auth.dart';
import 'music_service_form.dart';
import 'service_branding.dart';

/// Settings > Music services. Talks only to the FreeSounder control API.
class MusicServicesScreen extends ConsumerStatefulWidget {
  const MusicServicesScreen({super.key});

  @override
  ConsumerState<MusicServicesScreen> createState() => _MusicServicesScreenState();
}

class _MusicServicesScreenState extends ConsumerState<MusicServicesScreen> {
  MusicServicesCatalog? _catalog;
  SpotifyConnectStatus? _spotifyConnect;
  var _loading = true;
  String? _error;
  var _busy = false;

  bool get _demo => ref.read(playbackProvider).mockMode;

  @override
  void initState() {
    super.initState();
    Future.microtask(_reload);
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      if (_demo) {
        final mock = ref.read(playbackProvider.notifier).mockHousehold;
        setState(() {
          _catalog = MusicServicesCatalog(
            services: mock.serviceTypes,
            configured: mock.configuredServices,
          );
          _spotifyConnect = mock.spotifyConnect;
          _loading = false;
        });
        return;
      }
      final client = ref.read(playbackProvider.notifier).client;
      if (client == null) {
        setState(() {
          _loading = false;
          _error = 'Connect to a household first.';
        });
        return;
      }
      final catalog = await client.musicServicesCatalog();
      final connect = await client.spotifyConnectStatus();
      if (!mounted) {
        return;
      }
      setState(() {
        _catalog = catalog;
        _spotifyConnect = connect;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _loading = false;
        _error = error.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final catalog = _catalog;
    final configured = catalog?.configured ?? const <MusicService>[];
    final types = catalog?.services ?? const <MusicServiceType>[];
    final connectedDomains = {
      for (final item in configured)
        if (!types.any((type) => type.domain == item.domain && type.multiInstance)) item.domain,
    };
    final availableToAdd = [
      for (final item in types)
        if (item.availableOnServer && (item.multiInstance || !connectedDomains.contains(item.domain))) item,
    ];
    final unavailable = [
      for (final item in types)
        if (!item.availableOnServer) item,
    ];

    return Scaffold(
      appBar: AppBar(title: const Text('Music services')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _reload,
              child: ListView(
                cacheExtent: 4000,
                padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
                children: [
                  if (_error != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 16),
                      child: Text(_error!, style: text.bodyMedium?.copyWith(color: scheme.error)),
                    ),
                  Text('Connected', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 8),
                  if (configured.isEmpty)
                    Text(
                      'No music services yet. Add one below — you should not need the Music Assistant web page.',
                      style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
                    )
                  else
                    for (final service in configured)
                      ListTile(
                        key: Key('connected-${service.domain}'),
                        contentPadding: EdgeInsets.zero,
                        leading: ServiceMark(domain: service.domain),
                        title: Text(service.name),
                        subtitle: Text(
                          [
                            service.statusLabel,
                            if (service.account != null) service.account!,
                            if (service.limited) 'Limited',
                          ].join(' · '),
                        ),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => _openDetail(service),
                      ),
                  const SizedBox(height: 24),
                  Text('Add a service', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 4),
                  Text(
                    'Sign in from this app. Passwords and tokens stay on the household host.',
                    style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                  const SizedBox(height: 12),
                  GridView.count(
                    key: const Key('add-service-grid'),
                    crossAxisCount: 3,
                    shrinkWrap: true,
                    physics: const NeverScrollableScrollPhysics(),
                    mainAxisSpacing: 12,
                    crossAxisSpacing: 12,
                    childAspectRatio: 0.72,
                    children: [
                      for (final item in availableToAdd)
                        _AddTile(item: item, onTap: () => _add(item)),
                    ],
                  ),
                  if (unavailable.isNotEmpty) ...[
                    const SizedBox(height: 20),
                    Text('Not on this player', style: text.titleSmall?.copyWith(color: scheme.onSurfaceVariant)),
                    const SizedBox(height: 8),
                    for (final item in unavailable)
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: ServiceMark(domain: item.domain, size: 32),
                        title: Text(item.name),
                        subtitle: Text(item.description ?? 'Not available in this Music Assistant version.'),
                      ),
                  ],
                  if (_spotifyConnect != null && _spotifyConnect!.available) ...[
                    const SizedBox(height: 28),
                    Text('Spotify Connect', style: text.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
                    const SizedBox(height: 4),
                    Text(
                      _spotifyConnect!.note ??
                          'Each room can appear as a speaker in the official Spotify app.',
                      style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                    ),
                    if (_spotifyConnect!.engineLabel != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Text('Engine: ${_spotifyConnect!.engineLabel}', style: text.labelMedium),
                      ),
                    const SizedBox(height: 8),
                    if (!_spotifyConnect!.configured)
                      Text(
                        'Spotify Connect is available but not set up on this household yet.',
                        style: text.bodyMedium,
                      )
                    else
                      for (final room in _spotifyConnect!.rooms)
                        SwitchListTile(
                          key: Key('spotify-connect-${room.playerId}'),
                          contentPadding: EdgeInsets.zero,
                          title: Text(room.name),
                          value: room.enabled,
                          onChanged: _busy ? null : (value) => _toggleConnect(room.playerId, value),
                        ),
                  ],
                ],
              ),
            ),
    );
  }

  Future<void> _toggleConnect(String playerId, bool enabled) async {
    setState(() => _busy = true);
    try {
      if (_demo) {
        ref.read(playbackProvider.notifier).mockHousehold.setSpotifyConnect(playerId, enabled);
        setState(() => _spotifyConnect = ref.read(playbackProvider.notifier).mockHousehold.spotifyConnect);
        return;
      }
      final client = ref.read(playbackProvider.notifier).client;
      if (client == null) {
        return;
      }
      final status = await client.setSpotifyConnect(playerId, enabled);
      setState(() => _spotifyConnect = status);
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _openDetail(MusicService service) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => MusicServiceDetailScreen(service: service, onChanged: _reload),
      ),
    );
    await _reload();
  }

  Future<void> _add(MusicServiceType type) async {
    if (type.limited && type.limitation != null) {
      final proceed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(type.name),
          content: Text(type.limitation!),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Continue')),
          ],
        ),
      );
      if (proceed != true) {
        return;
      }
    }
    if (type.authKind == 'oauth') {
      await _runOAuth(domain: type.domain);
      return;
    }
    if (type.authKind == 'none') {
      await _addDirect(type.domain);
      return;
    }
    if (!mounted) {
      return;
    }
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => MusicServiceSetupScreen(type: type, onChanged: _reload),
      ),
    );
    await _reload();
  }

  Future<void> _addDirect(String domain) async {
    try {
      if (_demo) {
        ref.read(playbackProvider.notifier).mockHousehold.addService(domain);
        await _reload();
        return;
      }
      final client = ref.read(playbackProvider.notifier).client;
      if (client == null) {
        return;
      }
      final result = await client.addMusicService(domain);
      if (result['session_id'] != null && result['instance_id'] == null) {
        await _handleSession(AuthSession.fromJson(result), client);
      }
      await _reload();
    } catch (error) {
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _runOAuth({String? domain, String? instanceId}) async {
    if (_demo) {
      final added = ref.read(playbackProvider.notifier).mockHousehold.addService(domain ?? 'tidal');
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${added.name} is connected.')));
      await _reload();
      return;
    }
    final client = ref.read(playbackProvider.notifier).client;
    if (client == null) {
      return;
    }
    try {
      final session = await client.startMusicServiceAuth(domain: domain, instanceId: instanceId);
      await _handleSession(session, client);
      await _reload();
    } catch (error) {
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _handleSession(AuthSession session, ControlApiClient client) async {
    var current = session;
    if (current.status == 'awaiting_form') {
      if (!mounted) {
        return;
      }
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => MusicServiceSetupScreen(
            type: MusicServiceType(domain: current.domain, name: current.title ?? current.domain),
            session: current,
            onChanged: _reload,
          ),
        ),
      );
      return;
    }
    if (current.status == 'awaiting_browser') {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Finish signing in, then come back here.')),
        );
      }
      current = await completeExternalAuth(
        client: client,
        session: current,
        events: client.connectEvents(),
      );
    }
    if (!mounted) {
      return;
    }
    if (current.status == 'success') {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Service connected.')));
    } else if (current.error != null) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(current.error!)));
    }
  }
}

class _AddTile extends StatelessWidget {
  const _AddTile({required this.item, required this.onTap});

  final MusicServiceType item;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Material(
      color: Theme.of(context).colorScheme.surfaceContainerLow,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        key: Key('add-${item.domain}'),
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              ServiceMark(domain: item.domain, size: 36),
              const SizedBox(height: 6),
              Text(
                item.name,
                textAlign: TextAlign.center,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: text.labelMedium,
              ),
              if (item.limited)
                Text('Limited', style: text.labelSmall?.copyWith(color: Theme.of(context).colorScheme.tertiary)),
            ],
          ),
        ),
      ),
    );
  }
}

class MusicServiceDetailScreen extends ConsumerStatefulWidget {
  const MusicServiceDetailScreen({super.key, required this.service, required this.onChanged});

  final MusicService service;
  final Future<void> Function() onChanged;

  @override
  ConsumerState<MusicServiceDetailScreen> createState() => _MusicServiceDetailScreenState();
}

class _MusicServiceDetailScreenState extends ConsumerState<MusicServiceDetailScreen> {
  var _busy = false;

  bool get _demo => ref.read(playbackProvider).mockMode;

  @override
  Widget build(BuildContext context) {
    final service = widget.service;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: Text(service.name)),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
        children: [
          Row(
            children: [
              ServiceMark(domain: service.domain, size: 56),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(service.name, style: text.titleLarge),
                    const SizedBox(height: 4),
                    Text(service.statusLabel, style: text.bodyMedium?.copyWith(color: scheme.primary)),
                    if (service.account != null)
                      Text(service.account!, style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant)),
                  ],
                ),
              ),
            ],
          ),
          if (service.limitation != null) ...[
            const SizedBox(height: 16),
            Card(
              elevation: 0,
              color: scheme.surfaceContainerLow,
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Text(service.limitation!, style: text.bodyMedium),
              ),
            ),
          ],
          if (service.error != null) ...[
            const SizedBox(height: 12),
            Text(service.error!, style: text.bodyMedium?.copyWith(color: scheme.error)),
          ],
          const SizedBox(height: 28),
          if (service.canReauthenticate)
            ListTile(
              key: const Key('reauthenticate'),
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.login),
              title: const Text('Re-authenticate'),
              enabled: !_busy,
              onTap: _reauth,
            ),
          ListTile(
            key: const Key('remove-service'),
            contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.delete_outline, color: scheme.error),
            title: Text('Remove', style: TextStyle(color: scheme.error)),
            enabled: !_busy,
            onTap: _confirmRemove,
          ),
        ],
      ),
    );
  }

  Future<void> _reauth() async {
    setState(() => _busy = true);
    try {
      if (_demo) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Signed in again (demo).')));
        }
        return;
      }
      final client = ref.read(playbackProvider.notifier).client;
      if (client == null) {
        return;
      }
      final session = await client.startMusicServiceAuth(instanceId: widget.service.instanceId);
      if (!mounted) {
        return;
      }
      if (session.status == 'awaiting_browser') {
        await completeExternalAuth(client: client, session: session, events: client.connectEvents());
      }
      await widget.onChanged();
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _confirmRemove() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Remove ${widget.service.name}?'),
        content: const Text('This signs the household out of this service. You can add it again later.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(
            key: const Key('confirm-remove-service'),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (ok != true) {
      return;
    }
    setState(() => _busy = true);
    try {
      if (_demo) {
        ref.read(playbackProvider.notifier).mockHousehold.removeService(widget.service.instanceId);
      } else {
        final client = ref.read(playbackProvider.notifier).client;
        await client?.removeMusicService(widget.service.instanceId);
      }
      if (mounted) {
        Navigator.of(context).pop();
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }
}

class MusicServiceSetupScreen extends ConsumerStatefulWidget {
  const MusicServiceSetupScreen({super.key, required this.type, this.session, required this.onChanged});

  final MusicServiceType type;
  final AuthSession? session;
  final Future<void> Function() onChanged;

  @override
  ConsumerState<MusicServiceSetupScreen> createState() => _MusicServiceSetupScreenState();
}

class _MusicServiceSetupScreenState extends ConsumerState<MusicServiceSetupScreen> {
  var _busy = false;
  late List<ConfigField> _fields;
  Map<String, String> _errors = {};

  @override
  void initState() {
    super.initState();
    _fields = widget.session?.entries ?? _defaultFields(widget.type);
    _errors = widget.session?.errors ?? {};
  }

  List<ConfigField> _defaultFields(MusicServiceType type) {
    if (type.authKind == 'path' || type.domain == 'filesystem_local' || type.domain.startsWith('filesystem_')) {
      return const [
        ConfigField(key: 'path', type: 'string', label: 'Folder or share path', requiredField: true),
      ];
    }
    return const [
      ConfigField(key: 'username', type: 'string', label: 'Username', requiredField: true),
      ConfigField(key: 'password', type: 'secure_string', label: 'Password', requiredField: true, secret: true),
    ];
  }

  Future<void> _submit(Map<String, dynamic> values) async {
    setState(() => _busy = true);
    try {
      if (ref.read(playbackProvider).mockMode) {
        ref.read(playbackProvider.notifier).mockHousehold.addService(widget.type.domain, values: values);
        if (mounted) {
          Navigator.pop(context);
        }
        await widget.onChanged();
        return;
      }
      final client = ref.read(playbackProvider.notifier).client;
      if (client == null) {
        return;
      }
      if (widget.session != null) {
        final session = await client.submitMusicServiceAuth(widget.session!.sessionId, values);
        if (session.status != 'success' && session.status == 'awaiting_form') {
          setState(() {
            _fields = session.entries;
            _errors = session.errors;
          });
          return;
        }
      } else {
        await client.addMusicService(widget.type.domain, values: values);
      }
      if (mounted) {
        Navigator.pop(context);
      }
      await widget.onChanged();
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('Add ${widget.type.name}')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
        children: [
          if (widget.type.description != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 16),
              child: Text(widget.type.description!, style: Theme.of(context).textTheme.bodyMedium),
            ),
          MusicServiceForm(
            fields: _fields,
            errors: _errors,
            busy: _busy,
            submitLabel: 'Connect',
            onSubmit: _submit,
          ),
        ],
      ),
    );
  }
}
