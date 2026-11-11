import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Persisted household connection: last host URL and an explicit demo choice.
class ConnectionPrefs {
  ConnectionPrefs(this._prefs);

  final SharedPreferences _prefs;

  static const hostKey = 'host_url';
  static const demoKey = 'prefer_demo';
  static const developerKey = 'developer_mode';

  String? get savedHost {
    final value = _prefs.getString(hostKey);
    if (value == null || value.isEmpty) {
      return null;
    }
    return value;
  }

  bool get preferDemo => _prefs.getBool(demoKey) ?? false;

  bool get developerMode => _prefs.getBool(developerKey) ?? false;

  Future<void> setDeveloperMode(bool value) async {
    await _prefs.setBool(developerKey, value);
  }

  Future<void> setSavedHost(String? url) async {
    if (url == null || url.isEmpty) {
      await _prefs.remove(hostKey);
    } else {
      await _prefs.setString(hostKey, url);
    }
  }

  Future<void> setPreferDemo(bool value) async {
    await _prefs.setBool(demoKey, value);
  }
}

/// Overridden from `main` once SharedPreferences has loaded.
/// Tests leave this null so they do not touch the plugin.
final connectionPrefsProvider = Provider<ConnectionPrefs?>((ref) => null);

/// Hidden developer mode (7 taps on the app version). Persisted when
/// SharedPreferences is available. Shows raw Bluetooth step details.
class DeveloperModeNotifier extends Notifier<bool> {
  @override
  bool build() => ref.read(connectionPrefsProvider)?.developerMode ?? false;

  Future<void> set(bool value) async {
    state = value;
    await ref.read(connectionPrefsProvider)?.setDeveloperMode(value);
  }
}

final developerModeProvider = NotifierProvider<DeveloperModeNotifier, bool>(DeveloperModeNotifier.new);
