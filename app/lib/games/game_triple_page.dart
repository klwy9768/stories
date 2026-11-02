import 'dart:math';

import 'package:flutter/material.dart';

import '../util.dart';
import 'game_scores.dart';

class _Tile {
  final int kind;
  double x;
  double y;
  int layer;
  bool removed = false;
  _Tile(this.kind, this.x, this.y, this.layer);
}

/// 三连消：点击收集，三个相同自动消除；槽位满则失败（羊了个羊式玩法，原创实现）
class GameTriplePage extends StatefulWidget {
  const GameTriplePage({super.key});

  @override
  State<GameTriplePage> createState() => _GameTriplePageState();
}

class _GameTriplePageState extends State<GameTriplePage> {
  static const _emojis = ['🍎', '🍇', '🍉', '🍋', '🥕', '🌽', '🍄', '🥦', '🍑', '🍒'];
  final _rnd = Random();
  List<_Tile> _tiles = [];
  final List<int> _tray = [];
  int _level = 1;
  int _score = 0;
  int _best = 0;
  bool _over = false;

  int get _kindsCount => min(6 + (_level - 1), 10);
  // 棋盘总位置数为 25+16+9=50，每条三连占 3 格 → 最多 16 条，防止高关卡越界
  int get _triplesCount => min(6 + (_level - 1) * 2, 16);

  @override
  void initState() {
    super.initState();
    GameScores.best('triple').then((b) {
      if (mounted) setState(() => _best = b);
    });
    _startLevel(1, keepScore: false);
  }

  void _startLevel(int level, {bool keepScore = true}) {
    setState(() {
      _level = level;
      if (!keepScore) _score = 0;
      _tray.clear();
      _over = false;
      _tiles = _gen(_triplesCount, _kindsCount);
    });
  }

  List<_Tile> _gen(int triples, int kinds) {
    final total = triples * 3;
    final spots = <(double, double, int)>[];
    for (var l = 0; l < 3; l++) {
      final off = l * 0.5;
      final span = 5 - l;
      for (var gx = 0; gx < span; gx++) {
        for (var gy = 0; gy < span; gy++) {
          spots.add((off + gx, off + gy, l));
        }
      }
    }
    spots.shuffle(_rnd);
    final take = min(total, spots.length); // 防御：位置不足时不越界
    final chosen = spots.take(take).toList();
    final kindsList = <int>[];
    for (var i = 0; i < triples; i++) {
      final k = i % kinds;
      kindsList.addAll([k, k, k]);
    }
    kindsList.shuffle(_rnd);
    return [
      for (var i = 0; i < take; i++) _Tile(kindsList[i], chosen[i].$1, chosen[i].$2, chosen[i].$3)
    ];
  }

  bool _blocked(_Tile t) {
    for (final o in _tiles) {
      if (o.removed || identical(o, t)) continue;
      if (o.layer > t.layer && (o.x - t.x).abs() < 1 && (o.y - t.y).abs() < 1) return true;
    }
    return false;
  }

  void _tap(_Tile t) {
    if (_over || t.removed) return;
    if (_blocked(t)) {
      showToast(context, '被上面的牌压住了');
      return;
    }
    setState(() {
      t.removed = true;
      _insertTray(t.kind);
      _resolve();
    });
    if (_tiles.every((x) => x.removed)) {
      _levelClear();
      return;
    }
    if (_over) _showOver();
  }

  void _insertTray(int kind) {
    var idx = _tray.length;
    for (var i = _tray.length - 1; i >= 0; i--) {
      if (_tray[i] == kind) {
        idx = i + 1;
        break;
      }
    }
    _tray.insert(idx, kind);
  }

  void _resolve() {
    var changed = true;
    while (changed) {
      changed = false;
      final count = <int, int>{};
      for (final k in _tray) {
        count[k] = (count[k] ?? 0) + 1;
      }
      for (final e in count.entries) {
        if (e.value >= 3) {
          var removed = 0;
          _tray.removeWhere((k) {
            if (k == e.key && removed < 3) {
              removed++;
              return true;
            }
            return false;
          });
          _score += 10;
          changed = true;
          break;
        }
      }
    }
    if (_score > _best) {
      _best = _score;
      GameScores.save('triple', _best);
    }
    if (_tray.length >= 7) _over = true;
  }

  void _shuffleBoard() {
    if (_over) return;
    setState(() {
      final remaining = _tiles.where((t) => !t.removed).toList();
      final spots = [for (final t in remaining) (t.x, t.y, t.layer)];
      spots.shuffle(_rnd);
      for (var i = 0; i < remaining.length; i++) {
        remaining[i].x = spots[i].$1;
        remaining[i].y = spots[i].$2;
        remaining[i].layer = spots[i].$3;
      }
    });
  }

  void _levelClear() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      showDialog(
        context: context,
        barrierDismissible: false,
        builder: (ctx) => AlertDialog(
          title: Text('第 $_level 关完成！'),
          content: Text('当前得分：$_score'),
          actions: [
            TextButton(
                onPressed: () {
                  Navigator.pop(ctx);
                  _startLevel(_level + 1);
                },
                child: const Text('下一关')),
            TextButton(
                onPressed: () {
                  Navigator.pop(ctx);
                  _startLevel(1, keepScore: false);
                },
                child: const Text('重头再来')),
          ],
        ),
      );
    });
  }

  void _showOver() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      showDialog(
        context: context,
        barrierDismissible: false,
        builder: (ctx) => AlertDialog(
          title: const Text('槽位满了'),
          content: Text('本局得分：$_score\n最高分：$_best'),
          actions: [
            TextButton(
                onPressed: () {
                  Navigator.pop(ctx);
                  _startLevel(1, keepScore: false);
                },
                child: const Text('再来一局')),
          ],
        ),
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    // 层号低的先画（底层），层号高的后画（压在上面）
    final ordered = List.of(_tiles)..sort((a, b) => a.layer.compareTo(b.layer));
    return Scaffold(
      appBar: AppBar(
        title: Text('三连消  ·  第 $_level 关'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 10),
            child: Center(
                child: Text('$_score 分',
                    style: const TextStyle(fontSize: 13, color: Color(0xFF888888)))),
          ),
        ],
      ),
      body: Column(children: [
        Expanded(
          child: LayoutBuilder(builder: (ctx, cons) {
            final scale = min(cons.maxWidth / 5.4, cons.maxHeight / 5.4);
            final ox = (cons.maxWidth - scale * 5.0) / 2;
            final oy = (cons.maxHeight - scale * 5.0) / 2;
            return Stack(children: [
              for (final t in ordered)
                if (!t.removed)
                  Positioned(
                    left: ox + t.x * scale,
                    top: oy + t.y * scale,
                    width: scale,
                    height: scale,
                    child: GestureDetector(
                      onTap: () => _tap(t),
                      child: _chip(t, scale),
                    ),
                  ),
            ]);
          }),
        ),
        Container(
          color: Colors.white,
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            for (var i = 0; i < 7; i++)
              Container(
                width: 40,
                height: 40,
                margin: const EdgeInsets.symmetric(horizontal: 2),
                decoration: BoxDecoration(
                  color: i < _tray.length ? const Color(0xFFF7F7F7) : const Color(0xFFFAFAFA),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: const Color(0xFFE0E0E0)),
                ),
                alignment: Alignment.center,
                child: i < _tray.length
                    ? Text(_emojis[_tray[i]], style: const TextStyle(fontSize: 22))
                    : null,
              ),
          ]),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 14),
          child: Row(children: [
            const Expanded(
              child: Text('点击最上层的牌收进槽位，三个相同自动消除；槽位满了就输',
                  style: TextStyle(fontSize: 12, color: Color(0xFF999999))),
            ),
            TextButton(onPressed: _shuffleBoard, child: const Text('重排')),
            TextButton(
                onPressed: () => _startLevel(1, keepScore: false),
                child: const Text('重开')),
          ]),
        ),
      ]),
    );
  }

  Widget _chip(_Tile t, double scale) {
    final blocked = _blocked(t);
    return Container(
      margin: const EdgeInsets.all(1),
      decoration: BoxDecoration(
        color: blocked ? const Color(0xFFE9E9E9) : Colors.white,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: const Color(0xFFD9D9D9)),
        boxShadow: blocked
            ? null
            : const [BoxShadow(color: Color(0x22000000), blurRadius: 2, offset: Offset(0, 1))],
      ),
      alignment: Alignment.center,
      child: Text(_emojis[t.kind],
          style: TextStyle(fontSize: scale * 0.52, color: blocked ? Colors.black26 : null)),
    );
  }
}
