import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'game_scores.dart';

class _Bullet {
  double x;
  double y;
  _Bullet(this.x, this.y);
}

class _Enemy {
  double x;
  double y;
  final double vy;
  final double r;
  int hp;
  final int score;
  double sway;
  _Enemy(this.x, this.y, this.vy, this.r, this.hp, this.score, this.sway);
}

class _Particle {
  double x;
  double y;
  double vx;
  double vy;
  double life;
  final Color color;
  _Particle(this.x, this.y, this.vx, this.vy, this.life, this.color);
}

/// 星尘战机：拖动战机，自动开火，击落敌机（原创实现）
class GameShooterPage extends StatefulWidget {
  const GameShooterPage({super.key});

  @override
  State<GameShooterPage> createState() => _GameShooterPageState();
}

class _GameShooterPageState extends State<GameShooterPage> with SingleTickerProviderStateMixin {
  late final Ticker _ticker;
  final _rnd = Random();
  Size _size = Size.zero;
  Duration _last = Duration.zero;

  double _px = 0;
  double _py = 0;
  final List<_Bullet> _bullets = [];
  final List<_Enemy> _enemies = [];
  final List<_Particle> _parts = [];
  final List<Offset> _stars = [];

  double _fireAcc = 0;
  double _spawnAcc = 0;
  double _elapsed = 0;
  double _invuln = 0;
  int _lives = 3;
  int _score = 0;
  int _best = 0;
  bool _over = false;
  bool _paused = false;

  @override
  void initState() {
    super.initState();
    GameScores.best('shooter').then((b) {
      if (mounted) setState(() => _best = b);
    });
    _ticker = createTicker(_tick)..start();
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  void _initGame(Size size) {
    _size = size;
    _px = size.width / 2;
    _py = size.height - 110;
    _bullets.clear();
    _enemies.clear();
    _parts.clear();
    _stars.clear();
    for (var i = 0; i < 40; i++) {
      _stars.add(Offset(_rnd.nextDouble() * size.width, _rnd.nextDouble() * size.height));
    }
    _fireAcc = 0;
    _spawnAcc = 0;
    _elapsed = 0;
    _invuln = 1.0;
    _lives = 3;
    _score = 0;
    _over = false;
    _paused = false;
    _last = Duration.zero;
    if (!_ticker.isActive) _ticker.start(); // 暂停中点刷新后重新开始，避免画面卡死
  }

  void _tick(Duration now) {
    if (_size == Size.zero) return;
    if (_last == Duration.zero) {
      _last = now;
      return;
    }
    var dt = (now - _last).inMicroseconds / 1e6;
    _last = now;
    if (dt > 0.05) dt = 0.05;
    if (dt < 0) dt = 0; // 暂停恢复时避免负 dt（画面倒放/白送无敌）
    if (_over || _paused) return;

    _elapsed += dt;
    if (_invuln > 0) _invuln -= dt;

    // 开火
    _fireAcc += dt;
    while (_fireAcc >= 0.16) {
      _fireAcc -= 0.16;
      _bullets.add(_Bullet(_px, _py - 24));
    }

    // 敌机生成
    _spawnAcc += dt;
    final interval = max(0.34, 1.05 - _elapsed * 0.012);
    while (_spawnAcc >= interval) {
      _spawnAcc -= interval;
      final big = _rnd.nextDouble() < 0.16;
      final x = 24 + _rnd.nextDouble() * (_size.width - 48);
      _enemies.add(_Enemy(
        x,
        -30,
        70 + _rnd.nextDouble() * 60 + (_elapsed * 2).clamp(0, 90),
        big ? 22 : 13,
        big ? 3 : 1,
        big ? 30 : 10,
        _rnd.nextDouble() * 6.28,
      ));
    }

    // 子弹推进
    for (final b in _bullets) {
      b.y -= 620 * dt;
    }
    _bullets.removeWhere((b) => b.y < -20);

    // 敌机推进 + 碰撞
    for (final e in _enemies) {
      e.y += e.vy * dt;
      e.x += sin(e.sway + _elapsed * 2.2) * 40 * dt;
      e.x = e.x.clamp(16.0, _size.width - 16);
    }
    for (final e in List.of(_enemies)) {
      for (final b in List.of(_bullets)) {
        final d = sqrt(pow(b.x - e.x, 2) + pow(b.y - e.y, 2));
        if (d < e.r + 4) {
          _bullets.remove(b);
          e.hp -= 1;
          _boom(b.x, b.y, 5, const Color(0xFFFFD54F));
          if (e.hp <= 0) {
            _enemies.remove(e);
            _score += e.score;
            if (_score > _best) {
              _best = _score;
              GameScores.save('shooter', _best);
            }
            _boom(e.x, e.y, e.r > 16 ? 26 : 14, const Color(0xFFFF7043));
          }
          break;
        }
      }
    }
    // 敌机撞机
    for (final e in List.of(_enemies)) {
      final d = sqrt(pow(e.x - _px, 2) + pow(e.y - _py, 2));
      if (d < e.r + 14) {
        _enemies.remove(e);
        _boom(e.x, e.y, 16, const Color(0xFFFF5252));
        if (_invuln <= 0) {
          _lives -= 1;
          _invuln = 1.6;
          if (_lives <= 0) {
            _over = true;
            _showOver();
          }
        }
      }
    }
    _enemies.removeWhere((e) => e.y > _size.height + 40);

    // 粒子
    for (final p in _parts) {
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.life -= dt;
    }
    _parts.removeWhere((p) => p.life <= 0);

    // 星空
    for (var i = 0; i < _stars.length; i++) {
      final s = _stars[i];
      var y = s.dy + 40 * dt;
      if (y > _size.height) y = 0;
      _stars[i] = Offset(s.dx, y);
    }

    setState(() {});
  }

  void _boom(double x, double y, int n, Color color) {
    for (var i = 0; i < n; i++) {
      final a = _rnd.nextDouble() * 6.28;
      final sp = 40 + _rnd.nextDouble() * 160;
      _parts.add(_Particle(x, y, cos(a) * sp, sin(a) * sp, 0.4 + _rnd.nextDouble() * 0.4, color));
    }
  }

  void _showOver() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      showDialog(
        context: context,
        barrierDismissible: false,
        builder: (ctx) => AlertDialog(
          title: const Text('战机被击落'),
          content: Text('本局得分：$_score\n最高分：$_best'),
          actions: [
            TextButton(
                onPressed: () {
                  Navigator.pop(ctx);
                  if (_size != Size.zero) _initGame(_size);
                  setState(() {});
                },
                child: const Text('再来一局')),
          ],
        ),
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0B1026),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0B1026),
        foregroundColor: Colors.white,
        title: Text('星尘战机  ·  $_score'),
        actions: [
          IconButton(
              icon: Icon(_paused ? Icons.play_arrow : Icons.pause),
              onPressed: () => setState(() {
                    _paused = !_paused;
                    // 暂停时停掉 Ticker，避免 60Hz 空转耗电；继续时恢复
                    if (_paused) {
                      _ticker.stop();
                    } else if (!_over && !_ticker.isActive) {
                      _last = Duration.zero; // 重置帧计时，避免恢复首帧负 dt
                      _ticker.start();
                    }
                  })),
          IconButton(icon: const Icon(Icons.refresh), onPressed: () {
            if (_size != Size.zero) _initGame(_size);
            setState(() {});
          }),
          const SizedBox(width: 4),
        ],
      ),
      body: LayoutBuilder(builder: (ctx, cons) {
        final size = Size(cons.maxWidth, cons.maxHeight);
        if (_size == Size.zero) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (!mounted) return;
            _initGame(size);
            setState(() {});
          });
        }
        return GestureDetector(
          onPanUpdate: (d) {
            setState(() {
              _px = (_px + d.delta.dx).clamp(20.0, size.width - 20);
              _py = (_py + d.delta.dy).clamp(size.height * 0.35, size.height - 40);
            });
          },
          child: RepaintBoundary(
            child: CustomPaint(
              size: Size.infinite,
              painter: _ShooterPainter(
                size: size,
                px: _px,
                py: _py,
                bullets: _bullets,
                enemies: _enemies,
                parts: _parts,
                stars: _stars,
                lives: _lives,
                invuln: _invuln,
                paused: _paused,
              ),
            ),
          ),
        );
      }),
    );
  }
}

class _ShooterPainter extends CustomPainter {
  final Size size;
  final double px, py, invuln;
  final int lives;
  final bool paused;
  final List<_Bullet> bullets;
  final List<_Enemy> enemies;
  final List<_Particle> parts;
  final List<Offset> stars;

  _ShooterPainter({
    required this.size,
    required this.px,
    required this.py,
    required this.bullets,
    required this.enemies,
    required this.parts,
    required this.stars,
    required this.lives,
    required this.invuln,
    required this.paused,
  });

  @override
  void paint(Canvas canvas, Size s) {
    // 星空
    final starPaint = Paint()..color = Colors.white.withValues(alpha: 0.5);
    for (final st in stars) {
      canvas.drawCircle(st, 1.2, starPaint);
    }

    // 子弹
    final bp = Paint()..color = const Color(0xFF80D8FF);
    for (final b in bullets) {
      canvas.drawRRect(
          RRect.fromRectAndRadius(Rect.fromCenter(center: Offset(b.x, b.y), width: 4, height: 14),
              const Radius.circular(2)),
          bp);
    }

    // 敌机（原创几何形状）
    for (final e in enemies) {
      if (e.r > 16) {
        final p = Paint()..color = const Color(0xFFFF8A65);
        final path = Path()
          ..moveTo(e.x, e.y + e.r)
          ..lineTo(e.x - e.r, e.y - e.r * 0.6)
          ..lineTo(e.x + e.r, e.y - e.r * 0.6)
          ..close();
        canvas.drawPath(path, p);
        canvas.drawCircle(Offset(e.x, e.y + 2), e.r * 0.35, Paint()..color = const Color(0xFF4E342E));
      } else {
        canvas.drawCircle(Offset(e.x, e.y), e.r, Paint()..color = const Color(0xFFBA68C8));
        canvas.drawCircle(Offset(e.x, e.y), e.r * 0.45, Paint()..color = const Color(0xFF311B92));
      }
    }

    // 玩家战机（原创三角造型）
    final blink = invuln > 0 && (invuln * 10).floor() % 2 == 0;
    if (!blink) {
      final p = Paint()..color = const Color(0xFF4FC3F7);
      final path = Path()
        ..moveTo(px, py - 20)
        ..lineTo(px - 16, py + 14)
        ..lineTo(px - 5, py + 8)
        ..lineTo(px, py + 16)
        ..lineTo(px + 5, py + 8)
        ..lineTo(px + 16, py + 14)
        ..close();
      canvas.drawPath(path, p);
      canvas.drawCircle(Offset(px, py - 6), 4, Paint()..color = Colors.white);
    }

    // 粒子
    for (final pt in parts) {
      canvas.drawCircle(Offset(pt.x, pt.y), 2.4 * (pt.life / 0.8).clamp(0.2, 1.6),
          Paint()..color = pt.color.withValues(alpha: (pt.life / 0.8).clamp(0.0, 1.0)));
    }

    // 生命
    for (var i = 0; i < 3; i++) {
      canvas.drawCircle(
          Offset(20 + i * 22, 22), 7,
          Paint()..color = i < lives ? const Color(0xFFFF5252) : const Color(0x33FFFFFF));
    }

    if (paused) {
      final tp = TextPainter(
          text: const TextSpan(
              text: '已暂停',
              style: TextStyle(fontSize: 22, color: Colors.white, fontWeight: FontWeight.w600)),
          textDirection: TextDirection.ltr)
        ..layout();
      tp.paint(canvas, Offset((s.width - tp.width) / 2, s.height / 2 - 20));
    }
  }

  @override
  bool shouldRepaint(covariant _ShooterPainter old) => true;
}
