import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'game_scores.dart';

class _Plat {
  final double x;
  final double w;
  final double y;
  _Plat({required this.x, required this.w, required this.y});
}

/// 跳跳乐：按住蓄力、松开跳跃，落在平台上得分（原创实现）
class GameJumpPage extends StatefulWidget {
  const GameJumpPage({super.key});

  @override
  State<GameJumpPage> createState() => _GameJumpPageState();
}

class _GameJumpPageState extends State<GameJumpPage> with SingleTickerProviderStateMixin {
  static const _g = 16.0;
  late final Ticker _ticker;
  final _rnd = Random();

  final List<_Plat> _plats = [];
  double _px = 0.2;
  double _py = 0;
  double _vx = 0;
  double _vy = 0;
  bool _charging = false;
  bool _flying = false;
  bool _over = false;
  double _charge = 0;
  double _chargeDir = 1;
  double _camX = 0;
  int _score = 0;
  int _best = 0;
  double _perfectTimer = 0;
  Duration _last = Duration.zero;

  @override
  void initState() {
    super.initState();
    GameScores.best('jump').then((b) {
      if (mounted) setState(() => _best = b);
    });
    _ticker = createTicker(_tick); // 先创建 ticker 再 reset（_reset 里会读取并启动它）
    _reset();
    if (!_ticker.isActive) _ticker.start();
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  void _reset() {
    _plats.clear();
    _plats.add(_Plat(x: -1.4, w: 2.4, y: 0));
    _px = 0.2;
    _py = 0;
    _vx = 0;
    _vy = 0;
    _charging = false;
    _flying = false;
    _over = false;
    _charge = 0;
    _chargeDir = 1;
    _camX = 0;
    _score = 0;
    _perfectTimer = 0;
    _last = Duration.zero;
    _genNext();
    if (!_ticker.isActive) _ticker.start();
    setState(() {});
  }

  void _genNext() {
    var last = _plats.last;
    while (last.x + last.w < _px + 16) {
      final gap = 0.9 + _rnd.nextDouble() * (1.1 + min(_score, 24) * 0.045);
      final w = 1.1 + _rnd.nextDouble() * 0.9;
      final ny = (last.y + (_rnd.nextDouble() * 2 - 1) * 1.2).clamp(-2.5, 3.0);
      final np = _Plat(x: last.x + last.w + gap, w: w, y: ny);
      _plats.add(np);
      last = np;
    }
  }

  void _tick(Duration now) {
    if (_last == Duration.zero) {
      _last = now;
      return;
    }
    var dt = (now - _last).inMicroseconds / 1e6;
    _last = now;
    if (dt > 0.05) dt = 0.05;
    if (_over) return;
    var need = false;

    if (_perfectTimer > 0) {
      _perfectTimer -= dt;
      need = true;
    }
    if (_charging && !_flying) {
      _charge += _chargeDir * dt * 1.3;
      if (_charge >= 1) {
        _charge = 1;
        _chargeDir = -1;
      }
      if (_charge <= 0) {
        _charge = 0;
        _chargeDir = 1;
      }
      need = true;
    }
    if (_flying) {
      final prevY = _py;
      _vy += _g * dt;
      _px += _vx * dt;
      _py += _vy * dt;
      if (_vy > 0) {
        for (final p in _plats) {
          if (_px >= p.x && _px <= p.x + p.w && prevY <= p.y && _py >= p.y) {
            _py = p.y;
            _vx = 0;
            _vy = 0;
            _flying = false;
            _score++;
            final center = p.x + p.w / 2;
            if ((_px - center).abs() < 0.2) {
              _score += 1;
              _perfectTimer = 0.8;
            }
            if (_score > _best) {
              _best = _score;
              GameScores.save('jump', _best);
            }
            _genNext();
            break;
          }
        }
      }
      if (_py > 10 || _py < -8) {
        _over = true;
        _ticker.stop();
        _showOver();
      }
      need = true;
    }

    final targetCam = _px - 1.5;
    if (targetCam > _camX) {
      _camX += (targetCam - _camX) * min(1, dt * 5);
      need = true;
    }
    if (need) setState(() {});
  }

  void _press() {
    if (_over) return;
    setState(() {
      _charging = true;
      _charge = 0;
      _chargeDir = 1;
    });
  }

  void _release() {
    if (_over || _flying || !_charging) return;
    final power = 0.35 + _charge * 0.65;
    setState(() {
      _vx = 2.6 + power * 3.2;
      _vy = -(4.6 + power * 2.6);
      _flying = true;
      _charging = false;
      _charge = 0;
      _chargeDir = 1;
    });
  }

  void _showOver() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      showDialog(
        context: context,
        barrierDismissible: false,
        builder: (ctx) => AlertDialog(
          title: const Text('掉下去了'),
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
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('跳跳乐  ·  $_score'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 14),
            child: Center(
                child: Text('最高 $_best', style: const TextStyle(fontSize: 13, color: Color(0xFF888888)))),
          ),
        ],
      ),
      body: GestureDetector(
        onTapDown: (_) => _press(),
        onTapUp: (_) => _release(),
        onTapCancel: _release,
        child: RepaintBoundary(
          child: CustomPaint(
            size: Size.infinite,
            painter: _JumpPainter(
              plats: _plats,
              px: _px,
              py: _py,
              camX: _camX,
              charge: _charge,
              charging: _charging,
              flying: _flying,
              perfect: _perfectTimer,
            ),
          ),
        ),
      ),
      bottomNavigationBar: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
        child: Row(children: [
          const Expanded(
            child: Text('按住屏幕蓄力，松开起跳；落在平台中心得 2 分',
                style: TextStyle(fontSize: 12, color: Color(0xFF999999))),
          ),
          TextButton(onPressed: _reset, child: const Text('重开')),
        ]),
      ),
    );
  }
}

class _JumpPainter extends CustomPainter {
  final List<_Plat> plats;
  final double px, py, camX, charge;
  final bool charging, flying;
  final double perfect;

  _JumpPainter({
    required this.plats,
    required this.px,
    required this.py,
    required this.camX,
    required this.charge,
    required this.charging,
    required this.flying,
    required this.perfect,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final scale = size.width / 7.0;
    final originX = camX - 1.0;
    double sx(double wx) => (wx - originX) * scale;
    final baseY = size.height * 0.80;
    double sy(double wy) => baseY - wy * scale;

    // 天空
    final bg = Paint()
      ..shader = const LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [Color(0xFFB7E3FF), Color(0xFFEAF7FF)],
      ).createShader(Offset.zero & size);
    canvas.drawRect(Offset.zero & size, bg);

    // 远景云朵
    final cloud = Paint()..color = Colors.white.withValues(alpha: 0.7);
    for (var i = 0; i < 6; i++) {
      final cx = ((i * 3.7 - camX * 0.4) * scale) % (size.width + 160) - 80;
      final cy = 60.0 + (i % 3) * 70;
      canvas.drawOval(Rect.fromCenter(center: Offset(cx, cy), width: 120, height: 42), cloud);
    }

    // 平台
    for (final p in plats) {
      final r = Rect.fromLTRB(sx(p.x), sy(p.y), sx(p.x + p.w), sy(p.y - 0.45));
      if (r.right < -20 || r.left > size.width + 20) continue;
      final body = Paint()..color = const Color(0xFF7A5C3E);
      final top = Paint()..color = const Color(0xFF9AD06B);
      canvas.drawRRect(RRect.fromRectAndRadius(r, const Radius.circular(6)), body);
      canvas.drawRRect(
          RRect.fromRectAndRadius(
              Rect.fromLTRB(r.left, r.top, r.right, r.top + 10), const Radius.circular(6)),
          top);
    }

    // 角色（原创小人：圆角身体 + 眼睛 + 腮红）
    final squash = charging ? 1 - charge * 0.35 : 1.0;
    final bw = 0.5 * scale * (charging ? 1 + charge * 0.22 : 1);
    final bh = 0.68 * scale * squash;
    final footY = sy(py);
    final bodyRect = Rect.fromCenter(
        center: Offset(sx(px), footY - bh / 2 - 2), width: bw, height: bh);
    canvas.drawRRect(
        RRect.fromRectAndRadius(bodyRect, Radius.circular(bw * 0.45)),
        Paint()..color = const Color(0xFF3A3A3A));
    final eye = Paint()..color = Colors.white;
    canvas.drawCircle(Offset(bodyRect.center.dx - bw * 0.16, bodyRect.top + bh * 0.3), bw * 0.09, eye);
    canvas.drawCircle(Offset(bodyRect.center.dx + bw * 0.16, bodyRect.top + bh * 0.3), bw * 0.09, eye);
    final blush = Paint()..color = const Color(0xFFFFA0A0);
    canvas.drawCircle(Offset(bodyRect.center.dx - bw * 0.28, bodyRect.top + bh * 0.52), bw * 0.08, blush);
    canvas.drawCircle(Offset(bodyRect.center.dx + bw * 0.28, bodyRect.top + bh * 0.52), bw * 0.08, blush);

    // 蓄力条
    if (charging || flying) {
      final barW = size.width * 0.5;
      final barRect = Rect.fromLTWH((size.width - barW) / 2, 16, barW, 10);
      canvas.drawRRect(
          RRect.fromRectAndRadius(barRect, const Radius.circular(5)),
          Paint()..color = const Color(0x22000000));
      final fill = Rect.fromLTWH(barRect.left, barRect.top, barW * charge, 10);
      canvas.drawRRect(
          RRect.fromRectAndRadius(fill, const Radius.circular(5)),
          Paint()..color = const Color(0xFF07C160));
    }

    // 完美提示
    if (perfect > 0) {
      final tp = TextPainter(
        text: const TextSpan(
            text: '完美 +2',
            style: TextStyle(
                fontSize: 18, fontWeight: FontWeight.w700, color: Color(0xFFFF8C00))),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, Offset(sx(px) - tp.width / 2, footY - bh - 40));
    }
  }

  @override
  bool shouldRepaint(covariant _JumpPainter old) => true;
}
