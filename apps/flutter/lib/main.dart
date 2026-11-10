import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'theme/tokens.dart';
import 'ui/app_shell.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ProviderScope(child: FreesounderApp()));
}

class FreesounderApp extends StatelessWidget {
  const FreesounderApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'freesounder',
      theme: FsTokens.light(),
      darkTheme: FsTokens.dark(),
      home: const AppShell(),
      debugShowCheckedModeBanner: false,
    );
  }
}
