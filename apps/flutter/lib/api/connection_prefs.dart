import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Persisted household connection: last host URL and an explicit demo choice.
class ConnectionPrefs {
  ConnectionPrefs(this._prefs);

  final SharedPreferences _prefs;

  static const hostKey = 'host_url';
  static const demoKey = 'prefer_demo';

  String? get savedHost {
    final value = _prefs.getString(hostKey);
    if (value == null || value.isEmpty) {
      return null;
    }
    return value;
  }

  bool get preferDemo => _prefs.getBool(demoKey) ?? false;

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
