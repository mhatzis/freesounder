import 'package:flutter/material.dart';

/// One choice in the audio board picker.
class AudioBoardOption {
  const AudioBoardOption(this.id, this.title, this.group, {this.subtitle});

  final String id;
  final String title;
  final String? subtitle;
  final String group;
}

const audioBoardGroups = ['Automatic', 'DAC boards', 'Amp boards', 'USB, HDMI and headphone jack'];

/// Audio boards selectable per room (ids match freesounderd's DacChoice).
const audioBoardOptions = <AudioBoardOption>[
  AudioBoardOption(
    'auto',
    'Auto (recommended)',
    'Automatic',
    subtitle: 'Finds HATs, I2C DAC/amp boards and USB at boot; else HDMI or the headphone jack',
  ),
  AudioBoardOption('hifiberry-dacplus', 'HiFiBerry DAC+', 'DAC boards', subtitle: 'Also DAC2 Pro and DAC+ clones'),
  AudioBoardOption(
    'pifi-dacplus',
    'PiFi DAC+ 2.0',
    'DAC boards',
    subtitle: 'HiFiBerry DAC+ compatible (PCM5122, headphone jack)',
  ),
  AudioBoardOption('dacplus', 'Raspberry Pi DAC+', 'DAC boards'),
  AudioBoardOption('iqaudio-dacplus', 'IQaudio DAC+', 'DAC boards', subtitle: 'Also IQaudio DAC Zero'),
  AudioBoardOption(
    'hifiberry-dac',
    'PCM5102A DAC',
    'DAC boards',
    subtitle: 'HiFiBerry DAC, Pimoroni pHAT DAC, generic PCM5102A',
  ),
  AudioBoardOption('justboom-dac', 'JustBoom DAC / Amp', 'DAC boards'),
  AudioBoardOption('wm8960', 'WM8960 audio HAT', 'DAC boards', subtitle: 'Waveshare WM8960'),
  AudioBoardOption('hifiberry-amp2', 'HiFiBerry Amp2', 'Amp boards'),
  AudioBoardOption('hifiberry-amp3', 'HiFiBerry Amp3', 'Amp boards'),
  AudioBoardOption('hifiberry-amp4', 'HiFiBerry Amp4', 'Amp boards'),
  AudioBoardOption('hifiberry-amp4pro', 'HiFiBerry Amp4 Pro', 'Amp boards'),
  AudioBoardOption('hifiberry-amp100', 'HiFiBerry Amp100', 'Amp boards'),
  AudioBoardOption('digiampplus', 'Raspberry Pi DigiAMP+', 'Amp boards'),
  AudioBoardOption('iqaudio-digiampplus', 'IQaudio DigiAMP+', 'Amp boards'),
  AudioBoardOption(
    'pcm5122-amp',
    'PCM5122 amp HAT',
    'Amp boards',
    subtitle: 'Innomaker HiFi AMP, Waveshare and similar',
  ),
  AudioBoardOption('max98357a', 'MAX98357A I2S amp', 'Amp boards', subtitle: 'Adafruit, Pimoroni Audio Amp SHIM'),
  AudioBoardOption(
    'none',
    'No HAT: USB, HDMI or headphone jack',
    'USB, HDMI and headphone jack',
    subtitle: 'Skip board detection',
  ),
];

AudioBoardOption audioBoardFor(String? id) =>
    audioBoardOptions.firstWhere((o) => o.id == id, orElse: () => audioBoardOptions.first);

/// Full-height, searchable, grouped picker. Returns the chosen id, or null.
Future<String?> showAudioBoardPicker(BuildContext context, {required String current}) {
  return showModalBottomSheet<String>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    showDragHandle: true,
    builder: (context) => DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.9,
      minChildSize: 0.4,
      maxChildSize: 1,
      shouldCloseOnMinExtent: true,
      builder: (context, controller) => AudioBoardPicker(current: current, controller: controller),
    ),
  );
}

class AudioBoardPicker extends StatefulWidget {
  const AudioBoardPicker({super.key, required this.current, this.controller});

  final String current;
  final ScrollController? controller;

  @override
  State<AudioBoardPicker> createState() => _AudioBoardPickerState();
}

class _AudioBoardPickerState extends State<AudioBoardPicker> {
  var _query = '';

  bool _matches(AudioBoardOption o) {
    final q = _query.trim().toLowerCase();
    if (q.isEmpty) return true;
    return '${o.title} ${o.subtitle ?? ''} ${o.group}'.toLowerCase().contains(q);
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final shown = audioBoardOptions.where(_matches).toList();
    return CustomScrollView(
      key: const Key('dac-picker'),
      controller: widget.controller,
      slivers: [
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 8, 4),
            child: Row(
              children: [
                Expanded(
                  child: Text('Audio board', style: text.titleLarge?.copyWith(fontWeight: FontWeight.w600)),
                ),
                IconButton(
                  key: const Key('dac-picker-close'),
                  tooltip: 'Close',
                  icon: const Icon(Icons.close),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
          ),
        ),
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
            child: TextField(
              key: const Key('dac-picker-search'),
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                hintText: 'Search boards',
                border: OutlineInputBorder(),
                isDense: true,
              ),
              onChanged: (v) => setState(() => _query = v),
            ),
          ),
        ),
        for (final group in audioBoardGroups)
          if (shown.any((o) => o.group == group)) ...[
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(20, 12, 20, 4),
                child: Text(group, style: text.labelLarge?.copyWith(color: scheme.primary)),
              ),
            ),
            SliverList.list(
              children: [
                for (final o in shown.where((o) => o.group == group))
                  ListTile(
                    key: Key('dac-option-${o.id}'),
                    title: Text(o.title, maxLines: 1, overflow: TextOverflow.ellipsis),
                    subtitle: o.subtitle == null ? null : Text(o.subtitle!, maxLines: 2),
                    trailing: o.id == widget.current
                        ? Icon(Icons.check, key: const Key('dac-option-selected'), color: scheme.primary)
                        : null,
                    selected: o.id == widget.current,
                    onTap: () => Navigator.of(context).pop(o.id),
                  ),
              ],
            ),
          ],
        if (shown.isEmpty)
          const SliverToBoxAdapter(
            child: Padding(
              padding: EdgeInsets.all(24),
              child: Center(child: Text('No board matches')),
            ),
          ),
        const SliverToBoxAdapter(child: SizedBox(height: 24)),
      ],
    );
  }
}
