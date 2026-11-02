import 'dart:math';

import 'package:flutter/material.dart';

import 'game_scores.dart';

/// 数字合并（2048 玩法，原创实现）
class Game2048Page extends StatefulWidget {
  const Game2048Page({super.key});

  @override
  State<Game2048Page> createState() => _Game2048PageState();
}

class _Game2048PageState extends State<Game2048Page> {
  static const _n = 4;
  final _rnd = Random();
  List<List<int>> _board = [];
  int _score = 0;
  int _best = 0;
  bool _over = false;
  bool _celebrated = false;

  @override
  void initState() {
    super.initState();
    _reset();
    GameScores.best('2048').then((b) {
      if (mounted) setState(() => _best = b);
    });
  }

  void _reset() {
    _board = List.generate(_n, (_) => List.filled(_n, 0));
    _score = 0;
    _over = false;
    _celebrated = false;
    _spawn();
    _spawn();
    setState(() {});
  }

  void _spawn() {
    final empty = <(int, int)>[];
    for (var i = 0; i < _n; i++) {
      for (var j = 0; j < _n; j++) {
        if (_board[i][j] == 0) empty.add((i, j));
      }
    }
    if (empty.isEmpty) return;
    final p = empty[_rnd.nextInt(empty.length)];
    _board[p.$1][p.$2] = _rnd.nextInt(10) == 0 ? 4 : 2;
  }

  (int, List<int>) _mergeLine(List<int> line) {
    final vals = line.where((v) => v != 0).toList();
    final out = <int>[];
    var gained = 0;
    var i = 0;
    while (i < vals.length) {
      if (i + 1 < vals.length && vals[i] == vals[i + 1]) {
        final v = vals[i] * 2;
        out.add(v);
        gained += v;
        if (v >= 2048 && !_celebrated) {
          _celebrated = true;
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted) _showWin();
          });
        }
        i += 2;
      } else {
        out.add(vals[i]);
        i++;
      }
    }
    while (out.length < _n) {
      out.add(0);
    }
    return (gained, out);
  }

  bool _isOver() {
    for (var i = 0; i < _n; i++) {
      for (var j = 0; j < _n; j++) {
        if (_board[i][j] == 0) return false;
        if (i + 1 < _n && _board[i][j] == _board[i + 1][j]) return false;
        if (j + 1 < _n && _board[i][j] == _board[i][j + 1]) return false;
      }
    }
    return true;
  }

  void _move(int dx, int dy) {
    if (_over) return;
    final nb = List.generate(_n, (_) => List.filled(_n, 0));
    var gained = 0;
    for (var i = 0; i < _n; i++) {
      final line = <int>[];
      for (var k = 0; k < _n; k++) {
        if (dx == 1) {
          line.add(_board[i][_n - 1 - k]);
        } else if (dx == -1) {
          line.add(_board[i][k]);
        } else if (dy == 1) {
          line.add(_board[_n - 1 - k][i]);
        } else {
          line.add(_board[k][i]);
        }
      }
      final (g, merged) = _mergeLine(line);
      gained += g;
      for (var k = 0; k < _n; k++) {
        if (dx == 1) {
          nb[i][_n - 1 - k] = merged[k];
        } else if (dx == -1) {
          nb[i][k] = merged[k];
        } else if (dy == 1) {
          nb[_n - 1 - k][i] = merged[k];
        } else {
          nb[k][i] = merged[k];
        }
      }
    }
    var moved = false;
    for (var i = 0; i < _n; i++) {
      for (var j = 0; j < _n; j++) {
        if (nb[i][j] != _board[i][j]) moved = true;
      }
    }
    if (!moved) return;
    setState(() {
      _board = nb;
      _score += gained;
      _spawn();
      if (_score > _best) {
        _best = _score;
        GameScores.save('2048', _best);
      }
      if (_isOver()) _over = true;
    });
    if (_over) _showOver();
  }

  Color _tileColor(int v) {
    switch (v) {
      case 2:
        return const Color(0xFFEEE4DA);
      case 4:
        return const Color(0xFFEDE0C8);
      case 8:
        return const Color(0xFFF2B179);
      case 16:
        return const Color(0xFFF59563);
      case 32:
        return const Color(0xFFF67C5F);
      case 64:
        return const Color(0xFFF65E3B);
      case 128:
        return const Color(0xFFEDCF72);
      case 256:
        return const Color(0xFFEDCC61);
      case 512:
        return const Color(0xFFEDC850);
      case 1024:
        return const Color(0xFFEDC53F);
      case 2048:
        return const Color(0xFFEDC22E);
      default:
        return const Color(0xFF3C3A32);
    }
  }

  void _showOver() {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: const Text('游戏结束'),
        content: Text('本局得分：$_score\n最高分：$_best'),
        actions: [
          TextButton(
              onPressed: () {
                Navigator.pop(ctx);
                _reset();
              },
              child: const Text('再来一局')),
        ],
      ),
    );
  }

  void _showWin() {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('达成 2048！'),
        content: const Text('厉害！还能继续冲更大的数字。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('继续')),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('2048'),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), tooltip: '重新开始', onPressed: _reset),
          const SizedBox(width: 4),
        ],
      ),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
          child: Row(children: [
            _scoreCard('得分', _score),
            const SizedBox(width: 10),
            _scoreCard('最高分', _best),
          ]),
        ),
        Expanded(
          child: GestureDetector(
            onPanEnd: (d) {
              final v = d.velocity.pixelsPerSecond;
              if (v.dx.abs() > v.dy.abs()) {
                if (v.dx > 250) {
                  _move(1, 0);
                } else if (v.dx < -250) {
                  _move(-1, 0);
                }
              } else {
                if (v.dy > 250) {
                  _move(0, 1);
                } else if (v.dy < -250) {
                  _move(0, -1);
                }
              }
            },
            child: Center(
              child: AspectRatio(
                aspectRatio: 1,
                child: Container(
                  margin: const EdgeInsets.all(14),
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(
                      color: const Color(0xFFBBADA0), borderRadius: BorderRadius.circular(8)),
                  child: Column(children: [
                    for (var i = 0; i < _n; i++)
                      Expanded(
                        child: Row(children: [
                          for (var j = 0; j < _n; j++) Expanded(child: _tile(_board[i][j])),
                        ]),
                      ),
                  ]),
                ),
              ),
            ),
          ),
        ),
      ]),
    );
  }

  Widget _scoreCard(String label, int value) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 8),
        decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8)),
        child: Column(children: [
          Text(label, style: const TextStyle(fontSize: 12, color: Color(0xFF999999))),
          const SizedBox(height: 2),
          Text('$value', style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
        ]),
      ),
    );
  }

  Widget _tile(int v) {
    final digits = '$v'.length;
    return Container(
      margin: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        color: v == 0 ? const Color(0xFFCDC1B4) : _tileColor(v),
        borderRadius: BorderRadius.circular(5),
      ),
      alignment: Alignment.center,
      child: v == 0
          ? null
          : Text(
              '$v',
              style: TextStyle(
                fontSize: digits <= 2 ? 22 : digits == 3 ? 18 : 14,
                fontWeight: FontWeight.w700,
                color: v <= 4 ? const Color(0xFF776E65) : Colors.white,
              ),
            ),
    );
  }
}
