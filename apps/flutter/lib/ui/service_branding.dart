import 'package:flutter/material.dart';

/// Simple colour-and-icon marks. Official brand logos are not bundled.
class ServiceBranding {
  const ServiceBranding(this.icon, this.color);

  final IconData icon;
  final Color color;

  static ServiceBranding of(String domain) {
    switch (domain) {
      case 'spotify':
        return const ServiceBranding(Icons.graphic_eq, Color(0xFF1DB954));
      case 'tidal':
        return const ServiceBranding(Icons.waves, Color(0xFF000000));
      case 'apple_music':
        return const ServiceBranding(Icons.apple, Color(0xFFFA233B));
      case 'deezer':
        return const ServiceBranding(Icons.equalizer, Color(0xFFFF0099));
      case 'ytmusic':
        return const ServiceBranding(Icons.play_circle_fill, Color(0xFFFF0000));
      case 'qobuz':
        return const ServiceBranding(Icons.album, Color(0xFF2C2C2C));
      case 'soundcloud':
        return const ServiceBranding(Icons.cloud, Color(0xFFFF5500));
      case 'tunein':
        return const ServiceBranding(Icons.radio, Color(0xFF14D8CC));
      case 'radiobrowser':
        return const ServiceBranding(Icons.cell_tower, Color(0xFF3B82F6));
      case 'plex':
        return const ServiceBranding(Icons.movie, Color(0xFFE5A00D));
      case 'jellyfin':
        return const ServiceBranding(Icons.live_tv, Color(0xFF00A4DC));
      case 'opensubsonic':
        return const ServiceBranding(Icons.library_music, Color(0xFF2196F3));
      case 'filesystem_local':
        return const ServiceBranding(Icons.folder, Color(0xFF6B7280));
      case 'filesystem_smb':
        return const ServiceBranding(Icons.lan, Color(0xFF4B5563));
      case 'filesystem_nfs':
        return const ServiceBranding(Icons.dns, Color(0xFF4B5563));
      case 'spotify_connect':
        return const ServiceBranding(Icons.speaker_group, Color(0xFF1DB954));
      default:
        return const ServiceBranding(Icons.library_music_outlined, Color(0xFF2157B0));
    }
  }
}

class ServiceMark extends StatelessWidget {
  const ServiceMark({super.key, required this.domain, this.size = 40});

  final String domain;
  final double size;

  @override
  Widget build(BuildContext context) {
    final look = ServiceBranding.of(domain);
    final onDark = look.color.computeLuminance() < 0.25;
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: look.color,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Icon(look.icon, color: onDark ? Colors.white : Colors.white, size: size * 0.52),
    );
  }
}
