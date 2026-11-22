import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Persisted household connection: last host URL and an explicit demo choice.
class ConnectionPrefs {
  ConnectionPrefs(this._prefs);

  final SharedPreferences _prefs;

  static const hostKey = 'host_url';
  static const demoKey = 'prefer_demo';
  static const developerKey = 'developer_mode';
  static const wifiCountryKey = 'wifi_country';
  static const householdKey = 'household_id';
  static const membersKey = 'household_members';
  static const joinedKey = 'joined_households';

  /// Homes this phone has joined (0.6.19): set them up, joined with the
  /// home's code, or used them before the code existed.
  List<String> get joinedHouseholds => _prefs.getStringList(joinedKey) ?? const [];

  Future<void> addJoinedHousehold(String id) async {
    final joined = {...joinedHouseholds, id}.toList();
    await _prefs.setStringList(joinedKey, joined);
  }

  /// Household the saved host belonged to, and its members' last known URLs,
  /// so the app can find the home again when one player's IP changes.
  String? get householdId => _prefs.getString(householdKey);

  List<String> get knownMembers => _prefs.getStringList(membersKey) ?? const [];

  Future<void> setHousehold(String? id, {List<String>? members}) async {
    if (id == null) {
      await _prefs.remove(householdKey);
      await _prefs.remove(membersKey);
      return;
    }
    await _prefs.setString(householdKey, id);
    if (members != null) {
      await _prefs.setStringList(membersKey, members);
    }
  }

  /// Forget the saved home entirely (it was reset or reflashed).
  Future<void> forgetHome() async {
    await _prefs.remove(hostKey);
    await _prefs.remove(householdKey);
    await _prefs.remove(membersKey);
  }

  String? get savedHost {
    final value = _prefs.getString(hostKey);
    if (value == null || value.isEmpty) {
      return null;
    }
    return value;
  }

  bool get preferDemo => _prefs.getBool(demoKey) ?? false;

  String? get wifiCountry => _prefs.getString(wifiCountryKey);

  Future<void> setWifiCountry(String code) async {
    await _prefs.setString(wifiCountryKey, code);
  }

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
/// Developer mode (0.6.19): off every time the app starts, and seven taps on
/// the version switch it on or off. It is no longer saved (it used to stay on
/// for good once unlocked, showing debug lines on the home screen).
class DeveloperModeNotifier extends Notifier<bool> {
  @override
  bool build() {
    // Clear what older versions saved, so it is off again after an update.
    final prefs = ref.read(connectionPrefsProvider);
    if (prefs?.developerMode ?? false) {
      unawaited(prefs!.setDeveloperMode(false));
    }
    return false;
  }

  Future<void> set(bool value) async {
    state = value;
  }

  void toggle() => state = !state;
}

final developerModeProvider = NotifierProvider<DeveloperModeNotifier, bool>(DeveloperModeNotifier.new);
