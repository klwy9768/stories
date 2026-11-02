import 'package:flutter/material.dart';

import 'game_2048_page.dart';
import 'game_jump_page.dart';
import 'game_scores.dart';
import 'game_shooter_page.dart';
import 'game_triple_page.dart';

/// 游戏大厅（发现 → 游戏）
class GamesHubPage extends StatefulWidget {
  const GamesHubPage({super.key});

  @override
  State<GamesHubPage> createState() => _GamesHubPageState();
}

class _GamesHubPageState extends State<GamesHubPage> {
  Map<String, int> _bests = {};

  static const _games = <(String, String, String, String, Widget)>[
    ('2048', '2048', '🔢', '滑动合并，冲向 2048', Game2048Page()),
    ('triple', '三连消', '🀄', '三个相同即消除，槽满即败', GameTriplePage()),
    ('jump', '跳跳乐', '🐤', '按住蓄力，落到下一块平台', GameJumpPage()),
    ('shooter', '星尘战机', '🚀', '拖动战机，击落来袭敌机', GameShooterPage()),
  ];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final out = <String, int>{};
    for (final g in _games) {
      out[g.$1] = await GameScores.best(g.$1);
    }
    if (mounted) setState(() => _bests = out);
  }

  Future<void> _open(Widget page) async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => page));
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('游戏')),
      body: GridView.count(
        crossAxisCount: 2,
        padding: const EdgeInsets.all(14),
        mainAxisSpacing: 14,
        crossAxisSpacing: 14,
        childAspectRatio: 0.92,
        children: [
          for (final g in _games)
            GestureDetector(
              onTap: () => _open(g.$5),
              child: Container(
                decoration:
                    BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(14)),
                padding: const EdgeInsets.all(14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(g.$3, style: const TextStyle(fontSize: 34)),
                    const Spacer(),
                    Text(g.$2,
                        style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 4),
                    Text(g.$4,
                        style: const TextStyle(fontSize: 12, color: Color(0xFF999999)),
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis),
                    const SizedBox(height: 8),
                    Text('最高分 ${_bests[g.$1] ?? 0}',
                        style: const TextStyle(fontSize: 12, color: Color(0xFF576B95))),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}
