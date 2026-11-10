import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api/connection_prefs.dart';
import 'theme/tokens.dart';
import 'ui/app_shell.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final prefs = ConnectionPrefs(await SharedPreferences.getInstance());
  runApp(
    ProviderScope(
      overrides: [
        connectionPrefsProvider.overrideWithValue(prefs),
      ],
      child: const FreesounderApp(),
    ),
  );
}

class FreesounderApp extends StatelessWidget {
  const FreesounderApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'FreeSounder',
      theme: FsTokens.light(),
      darkTheme: FsTokens.dark(),
      home: const AppShell(),
      debugShowCheckedModeBanner: false,
    );
  }
}
