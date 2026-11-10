import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/playback_store.dart';
import '../theme/tokens.dart';
import 'mini_player.dart';
import 'room_settings_sheet.dart';
import 'rooms_panel.dart';
import 'settings_screen.dart';

/// App shell: now-playing canvas, persistent mini-player, rooms bottom sheet
/// on narrow widths, and a right-hand rooms rail at 900px and above.
class AppShell extends ConsumerStatefulWidget {
  const AppShell({super.key});

  @override
  ConsumerState<AppShell> createState() => _AppShellState();
}

class _AppShellState extends ConsumerState<AppShell> {
  final DraggableScrollableController _sheetController = DraggableScrollableController();

  @override
  void dispose() {
    _sheetController.dispose();
    super.dispose();
  }

  Future<void> _openRooms({double size = FsTokens.sheetHalf}) async {
    if (!_sheetController.isAttached) {
      return;
    }
    await _sheetController.animateTo(
      size,
      duration: const Duration(milliseconds: 280),
      curve: Curves.easeOutCubic,
    );
  }

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.sizeOf(context).width;
    final useRail = width >= FsTokens.railBreakpoint;
    final playback = ref.watch(playbackProvider);

    final canvas = _NowPlayingCanvas(state: playback);

    if (useRail) {
      return Scaffold(
        body: Row(
          children: [
            Expanded(
              child: Column(
                children: [
                  Expanded(child: canvas),
                  const MiniPlayer(showRoomsChip: false),
                ],
              ),
            ),
            VerticalDivider(width: 1, color: Theme.of(context).colorScheme.outlineVariant),
            const SizedBox(
              width: FsTokens.railWidth,
              child: RoomsPanel(asRail: true),
            ),
          ],
        ),
      );
    }

    return Scaffold(
      body: Column(
        children: [
          Expanded(
            child: Stack(
              children: [
                Column(
                  children: [
                    Expanded(child: canvas),
                    // Reserve collapsed-sheet height so content is not hidden.
                    SizedBox(height: MediaQuery.sizeOf(context).height * FsTokens.sheetCollapsed),
                  ],
                ),
                RoomsBottomSheet(controller: _sheetController),
              ],
            ),
          ),
          MiniPlayer(onRoomsPressed: _openRooms),
        ],
      ),
    );
  }
}


class _NowPlayingCanvas extends StatelessWidget {
  const _NowPlayingCanvas({required this.state});

  final PlaybackState state;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final np = state.nowPlaying;
    final title = np?.title ?? (state.discovering ? 'Looking for players…' : 'Nothing playing');
    final subtitle = [np?.artist, np?.album].whereType<String>().where((s) => s.isNotEmpty).join(' — ');
    final progress = (np?.duration != null && np!.duration! > 0)
        ? ((np.position ?? 0) / np.duration!).clamp(0.0, 1.0)
        : null;

    return ColoredBox(
      color: scheme.surface,
      child: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 8, 12, 16),
          child: Column(
            children: [
              _TopBar(state: state),
              Expanded(
                child: LayoutBuilder(
                  builder: (context, constraints) {
                    final artSize = (constraints.maxHeight * 0.48)
                        .clamp(96.0, constraints.maxWidth.clamp(96.0, 420.0));
                    return SingleChildScrollView(
                      child: ConstrainedBox(
                        constraints: BoxConstraints(minHeight: constraints.maxHeight),
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            SizedBox(
                              width: artSize,
                              height: artSize,
                              child: DecoratedBox(
                                decoration: BoxDecoration(
                                  borderRadius: BorderRadius.circular(28),
                                  boxShadow: [
                                    BoxShadow(
                                      color: FsTokens.seed.withValues(alpha: 0.28),
                                      blurRadius: 32,
                                      offset: const Offset(0, 16),
                                    ),
                                  ],
                                ),
                                child: ClipRRect(
                                  borderRadius: BorderRadius.circular(28),
                                  child: Image.asset(
                                    'assets/logo.png',
                                    fit: BoxFit.cover,
                                    filterQuality: FilterQuality.medium,
                                  ),
                                ),
                              ),
                            ),
                            const SizedBox(height: 24),
                            Text(
                              title,
                              textAlign: TextAlign.center,
                              style: text.headlineSmall?.copyWith(fontWeight: FontWeight.w600),
                            ),
                            if (subtitle.isNotEmpty) ...[
                              const SizedBox(height: 8),
                              Text(
                                subtitle,
                                textAlign: TextAlign.center,
                                style: text.bodyLarge?.copyWith(color: scheme.onSurfaceVariant),
                              ),
                            ],
                            if (state.activeRoom != null) ...[
                              const SizedBox(height: 4),
                              TextButton.icon(
                                key: const Key('now-playing-room-settings'),
                                onPressed: () => showRoomSettingsSheet(
                                  context,
                                  roomId: state.activeRoom!.id,
                                  roomName: state.activeRoom!.name,
                                ),
                                icon: const Icon(Icons.tune, size: 18),
                                label: Text(state.activeRoom!.name),
                              ),
                            ],
                            if (progress != null) ...[
                              const SizedBox(height: 16),
                              ClipRRect(
                                borderRadius: BorderRadius.circular(99),
                                child: LinearProgressIndicator(
                                  value: progress,
                                  minHeight: 3,
                                  backgroundColor: scheme.surfaceContainerHighest,
                                ),
                              ),
                            ],
                            if (state.discovering) ...[
                              const SizedBox(height: 24),
                              const SizedBox(
                                width: 20,
                                height: 20,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              ),
                            ],
                            if (state.error != null && !state.mockMode) ...[
                              const SizedBox(height: 16),
                              Text(state.error!, style: TextStyle(color: scheme.error)),
                            ],
                          ],
                        ),
                      ),
                    );
                  },
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _TopBar extends StatelessWidget {
  const _TopBar({required this.state});

  final PlaybackState state;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Row(
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(9),
          child: Image.asset('assets/logo.png', width: 32, height: 32, fit: BoxFit.cover),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Text(
            'FreeSounder',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600),
          ),
        ),
        if (state.mockMode && !state.discovering)
          Padding(
            padding: const EdgeInsets.only(right: 4),
            child: DecoratedBox(
              decoration: BoxDecoration(
                color: scheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(99),
              ),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                child: Text(
                  'Demo',
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    color: scheme.onSurfaceVariant,
                  ),
                ),
              ),
            ),
          ),
        IconButton(
          key: const Key('settings-button'),
          tooltip: 'Settings',
          icon: const Icon(Icons.settings_outlined),
          onPressed: () {
            Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const SettingsScreen()),
            );
          },
        ),
      ],
    );
  }
}
