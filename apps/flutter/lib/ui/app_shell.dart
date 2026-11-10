import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/playback_store.dart';
import '../theme/tokens.dart';
import 'mini_player.dart';
import 'rooms_panel.dart';

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
    final np = playback.nowPlaying;

    final canvas = ColoredBox(
      color: Theme.of(context).colorScheme.surface,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.album, size: 96, color: Theme.of(context).colorScheme.primary),
              const SizedBox(height: 16),
              Text(np?.title ?? 'Nothing playing', style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 4),
              Text(
                [np?.artist, np?.album].where((s) => s != null && s.isNotEmpty).join(' — '),
                style: Theme.of(context).textTheme.bodyLarge,
              ),
              if (playback.error != null) ...[
                const SizedBox(height: 16),
                Text(playback.error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ],
            ],
          ),
        ),
      ),
    );

    if (useRail) {
      return Scaffold(
        body: Row(
          children: [
            Expanded(
              child: Column(
                children: [
                  Expanded(child: canvas),
                  MiniPlayer(showRoomsChip: false),
                ],
              ),
            ),
            const VerticalDivider(width: 1),
            const SizedBox(
              width: FsTokens.railWidth,
              child: RoomsPanel(asRail: true),
            ),
          ],
        ),
      );
    }

    return Scaffold(
      body: Stack(
        children: [
          Column(
            children: [
              Expanded(child: canvas),
              // Reserve collapsed-sheet height so content is not hidden.
              SizedBox(height: MediaQuery.sizeOf(context).height * FsTokens.sheetCollapsed),
            ],
          ),
          RoomsBottomSheet(controller: _sheetController),
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: MiniPlayer(onRoomsPressed: _openRooms),
          ),
        ],
      ),
    );
  }
}
