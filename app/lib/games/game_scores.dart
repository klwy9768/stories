import 'package:shared_preferences/shared_preferences.dart';

/// 小游戏最高分存取
class GameScores {
  static Future<int> best(String key) async {
    try {
      final p = await SharedPreferences.getInstance();
      return p.getInt('game_best_$key') ?? 0;
    } catch (_) {
      return 0;
    }
  }

  static Future<void> save(String key, int v) async {
    try {
      final p = await SharedPreferences.getInstance();
      final cur = p.getInt('game_best_$key') ?? 0;
      if (v > cur) await p.setInt('game_best_$key', v);
    } catch (_) {}
  }
}
