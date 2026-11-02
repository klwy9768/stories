import 'dart:math' as math;

import 'package:flutter/material.dart';

/// 微信风格原创图标集（根据微信视觉语言自绘：24dp 网格、细描边、圆头圆角；
/// 所有路径均为原创绘制，未使用任何第三方美术文件）
const Color kWcIconInk = Color(0xFF181818);
const Color kWcIconGreen = Color(0xFF07C160);

abstract class _WcBase extends CustomPainter {
  final Color color;
  final bool active;
  const _WcBase(this.color, {this.active = false});

  Paint strokePaint(double w) => Paint()
    ..color = color
    ..style = PaintingStyle.stroke
    ..strokeWidth = w
    ..strokeCap = StrokeCap.round
    ..strokeJoin = StrokeJoin.round;

  Paint fillPaint() => Paint()
    ..color = color
    ..style = PaintingStyle.fill;
}

/// 返回箭头（纯 V 形折角）
class WcBackIcon extends StatelessWidget {
  final double size;
  final Color color;
  const WcBackIcon({super.key, this.size = 24, this.color = kWcIconInk});

  @override
  Widget build(BuildContext context) =>
      SizedBox(width: size, height: size, child: CustomPaint(painter: _BackPainter(color)));
}

class _BackPainter extends _WcBase {
  _BackPainter(super.color);

  @override
  void paint(Canvas canvas, Size s) {
    canvas.scale(s.width / 24);
    final p = strokePaint(2.2);
    final path = Path()
      ..moveTo(15, 4.6)
      ..lineTo(7.6, 12)
      ..lineTo(15, 19.4);
    canvas.drawPath(path, p);
  }

  @override
  bool shouldRepaint(covariant _BackPainter o) => o.color != color;
}

/// 三个圆点（聊天信息/更多）
class WcMoreIcon extends StatelessWidget {
  final double size;
  final Color color;
  const WcMoreIcon({super.key, this.size = 24, this.color = kWcIconInk});

  @override
  Widget build(BuildContext context) =>
      SizedBox(width: size, height: size, child: CustomPaint(painter: _MorePainter(color)));
}

class _MorePainter extends _WcBase {
  _MorePainter(super.color);

  @override
  void paint(Canvas canvas, Size s) {
    canvas.scale(s.width / 24);
    final p = fillPaint();
    for (final x in const [5.4, 12.0, 18.6]) {
      canvas.drawCircle(Offset(x, 12), 2.0, p);
    }
  }

  @override
  bool shouldRepaint(covariant _MorePainter o) => o.color != color;
}

/// 放大镜（搜索）
class WcSearchIcon extends StatelessWidget {
  final double size;
  final Color color;
  const WcSearchIcon({super.key, this.size = 24, this.color = kWcIconInk});

  @override
  Widget build(BuildContext context) =>
      SizedBox(width: size, height: size, child: CustomPaint(painter: _SearchPainter(color)));
}

class _SearchPainter extends _WcBase {
  _SearchPainter(super.color);

  @override
  void paint(Canvas canvas, Size s) {
    canvas.scale(s.width / 24);
    final p = strokePaint(1.9);
    canvas.drawCircle(const Offset(10.8, 10.8), 6.3, p);
    canvas.drawLine(const Offset(15.5, 15.5), const Offset(20.2, 20.2), p);
  }

  @override
  bool shouldRepaint(covariant _SearchPainter o) => o.color != color;
}

/// 圆圈加号
class WcPlusCircleIcon extends StatelessWidget {
  final double size;
  final Color color;
  const WcPlusCircleIcon({super.key, this.size = 24, this.color = kWcIconInk});

  @override
  Widget build(BuildContext context) =>
      SizedBox(width: size, height: size, child: CustomPaint(painter: _PlusCirclePainter(color)));
}

class _PlusCirclePainter extends _WcBase {
  _PlusCirclePainter(super.color);

  @override
  void paint(Canvas canvas, Size s) {
    canvas.scale(s.width / 24);
    final p = strokePaint(1.7);
    canvas.drawCircle(const Offset(12, 12), 9.5, p);
    canvas.drawLine(const Offset(12, 7.4), const Offset(12, 16.6), p);
    canvas.drawLine(const Offset(7.4, 12), const Offset(16.6, 12), p);
  }

  @override
  bool shouldRepaint(covariant _PlusCirclePainter o) => o.color != color;
}

/// 笑脸（表情面板开关）
class WcSmileyIcon extends StatelessWidget {
  final double size;
  final Color color;
  const WcSmileyIcon({super.key, this.size = 24, this.color = kWcIconInk});

  @override
  Widget build(BuildContext context) =>
      SizedBox(width: size, height: size, child: CustomPaint(painter: _SmileyPainter(color)));
}

class _SmileyPainter extends _WcBase {
  _SmileyPainter(super.color);

  @override
  void paint(Canvas canvas, Size s) {
    canvas.scale(s.width / 24);
    final p = strokePaint(1.7);
    canvas.drawCircle(const Offset(12, 12), 9.5, p);
    final f = fillPaint();
    canvas.drawCircle(const Offset(8.7, 10.1), 1.25, f);
    canvas.drawCircle(const Offset(15.3, 10.1), 1.25, f);
    canvas.drawArc(Rect.fromCircle(center: const Offset(12, 11.6), radius: 6.1),
        math.pi * 0.18, math.pi * 0.64, false, p);
  }

  @override
  bool shouldRepaint(covariant _SmileyPainter o) => o.color != color;
}

/// 键盘
class WcKeyboardIcon extends StatelessWidget {
  final double size;
  final Color color;
  const WcKeyboardIcon({super.key, this.size = 24, this.color = kWcIconInk});

  @override
  Widget build(BuildContext context) =>
      SizedBox(width: size, height: size, child: CustomPaint(painter: _KeyboardPainter(color)));
}

class _KeyboardPainter extends _WcBase {
  _KeyboardPainter(super.color);

  @override
  void paint(Canvas canvas, Size s) {
    canvas.scale(s.width / 24);
    final p = strokePaint(1.6);
    canvas.drawRRect(
        RRect.fromRectAndRadius(const Rect.fromLTWH(2.6, 6.0, 18.8, 12.0), const Radius.circular(2.2)), p);
    final f = fillPaint();
    for (final y in const [10.3, 13.4]) {
      for (final x in const [6.4, 9.6, 12.8, 16.0]) {
        canvas.drawCircle(Offset(x, y), 0.85, f);
      }
    }
    canvas.drawLine(const Offset(8.2, 15.9), const Offset(15.8, 15.9), p);
  }

  @override
  bool shouldRepaint(covariant _KeyboardPainter o) => o.color != color;
}

/// 语音输入开关（圆圈+声波）
class WcVoiceCircleIcon extends StatelessWidget {
  final double size;
  final Color color;
  const WcVoiceCircleIcon({super.key, this.size = 24, this.color = kWcIconInk});

  @override
  Widget build(BuildContext context) =>
      SizedBox(width: size, height: size, child: CustomPaint(painter: _VoiceCirclePainter(color)));
}

class _VoiceCirclePainter extends _WcBase {
  _VoiceCirclePainter(super.color);

  @override
  void paint(Canvas canvas, Size s) {
    canvas.scale(s.width / 24);
    final p = strokePaint(1.6);
    canvas.drawCircle(const Offset(12, 12), 10.2, p);
    final f = fillPaint();
    canvas.drawCircle(const Offset(12, 12), 1.9, f);
    for (final r in const [4.4, 7.4]) {
      canvas.drawArc(Rect.fromCircle(center: const Offset(12, 12), radius: r),
          -math.pi * 0.34, math.pi * 0.68, false, p);
      canvas.drawArc(Rect.fromCircle(center: const Offset(12, 12), radius: r),
          math.pi * 0.66, math.pi * 0.68, false, p);
    }
  }

  @override
  bool shouldRepaint(covariant _VoiceCirclePainter o) => o.color != color;
}

/// 小话筒（输入框内嵌）
class WcMicSmallIcon extends StatelessWidget {
  final double size;
  final Color color;
  const WcMicSmallIcon({super.key, this.size = 20, this.color = const Color(0xFFB2B2B2)});

  @override
  Widget build(BuildContext context) =>
      SizedBox(width: size, height: size, child: CustomPaint(painter: _MicSmallPainter(color)));
}

class _MicSmallPainter extends _WcBase {
  _MicSmallPainter(super.color);

  @override
  void paint(Canvas canvas, Size s) {
    canvas.scale(s.width / 24);
    final p = strokePaint(1.6);
    canvas.drawRRect(
        RRect.fromRectAndRadius(const Rect.fromLTWH(9.6, 4.4, 4.8, 9.2), const Radius.circular(2.4)), p);
    final holder = Path()
      ..moveTo(6.6, 11.6)
      ..quadraticBezierTo(6.6, 16.6, 12, 16.6)
      ..quadraticBezierTo(17.4, 16.6, 17.4, 11.6);
    canvas.drawPath(holder, p);
    canvas.drawLine(const Offset(12, 16.6), const Offset(12, 19.4), p);
    canvas.drawLine(const Offset(9.4, 19.6), const Offset(14.6, 19.6), p);
  }

  @override
  bool shouldRepaint(covariant _MicSmallPainter o) => o.color != color;
}

/// 底部导航图标（0=微信气泡 1=通讯录 2=发现 3=我）
class WcTabIcon extends StatelessWidget {
  final int index;
  final bool active;
  final double size;
  const WcTabIcon({super.key, required this.index, required this.active, this.size = 26});

  @override
  Widget build(BuildContext context) {
    final color = active ? kWcIconGreen : kWcIconInk;
    return SizedBox(
        width: size, height: size, child: CustomPaint(painter: _TabPainter(index, active, color)));
  }
}

class _TabPainter extends _WcBase {
  final int index;
  _TabPainter(this.index, bool active, Color color) : super(color, active: active);

  void _tail(Canvas c, Paint p, double x, double y, {required bool right}) {
    final path = Path();
    if (right) {
      path
        ..moveTo(x, y)
        ..lineTo(x + (active ? 3.6 : 3.2), y + (active ? 3.2 : 3.0))
        ..lineTo(x - 3.0, y + 0.4)
        ..close();
    } else {
      path
        ..moveTo(x, y)
        ..lineTo(x - (active ? 3.6 : 3.2), y + (active ? 3.2 : 3.0))
        ..lineTo(x + 3.0, y + 0.4)
        ..close();
    }
    if (active) {
      c.drawPath(path, p);
    } else {
      c.drawPath(path, strokePaint(1.6));
    }
  }

  @override
  void paint(Canvas canvas, Size s) {
    canvas.scale(s.width / 24);
    final line = strokePaint(1.65);
    final solid = fillPaint();

    if (index == 0) {
      // 两个交叠的气泡
      final big = RRect.fromRectAndRadius(
          const Rect.fromLTWH(2.4, 4.0, 13.6, 11.6), const Radius.circular(3.4));
      final small = RRect.fromRectAndRadius(
          const Rect.fromLTWH(12.2, 9.0, 9.4, 8.4), const Radius.circular(2.8));
      if (!active) {
        canvas.drawRRect(big, line);
        canvas.drawRRect(small, line);
        _tail(canvas, line, 6.2, 15.6, right: false);
        _tail(canvas, line, 18.0, 17.4, right: true);
      } else {
        canvas.drawRRect(big, solid);
        _tail(canvas, solid, 6.2, 15.6, right: false);
        canvas.drawRRect(small, Paint()..color = Colors.white);
        canvas.drawRRect(small, solid..style = PaintingStyle.stroke..strokeWidth = 1.6);
        final back = fillPaint();
        _tail(canvas, back, 18.0, 17.4, right: true);
      }
    } else if (index == 1) {
      // 人形 + 三条横线
      final head = const Offset(9.2, 7.9);
      final shoulders = Path()
        ..moveTo(3.2, 19.6)
        ..quadraticBezierTo(4.2, 13.6, 9.2, 13.6)
        ..quadraticBezierTo(14.2, 13.6, 15.2, 19.6);
      for (final y in const [7.7, 11.9, 16.1]) {
        if (active) {
          canvas.drawRRect(
              RRect.fromRectAndRadius(
                  Rect.fromLTWH(16.6, y - 1.0, 4.6, 2.0), const Radius.circular(1.0)),
              solid);
        } else {
          canvas.drawLine(Offset(16.8, y), Offset(20.8, y), line);
        }
      }
      if (active) {
        canvas.drawCircle(head, 3.6, solid);
        canvas.drawPath(
            (Path()
                  ..addPath(shoulders, Offset.zero)
                  ..lineTo(15.2, 19.6)
                  ..lineTo(3.2, 19.6)
                  ..close()),
            solid);
      } else {
        canvas.drawCircle(head, 3.4, line);
        canvas.drawPath(shoulders, line);
      }
    } else if (index == 2) {
      // 指南针
      canvas.drawCircle(const Offset(12, 12), 9.6, active ? solid : line);
      final needle = Path()
        ..moveTo(12, 2.4)
        ..lineTo(14.9, 12)
        ..lineTo(12, 21.6)
        ..lineTo(9.1, 12)
        ..close();
      canvas.save();
      canvas.translate(12, 12);
      canvas.rotate(-math.pi / 4);
      canvas.translate(-12, -12);
      if (active) {
        canvas.drawPath(needle, Paint()..color = Colors.white);
      } else {
        canvas.drawPath(needle, line);
      }
      canvas.restore();
    } else {
      // 我（人形）
      final shoulders = Path()
        ..moveTo(3.8, 19.6)
        ..quadraticBezierTo(5.2, 13.4, 12, 13.4)
        ..quadraticBezierTo(18.8, 13.4, 20.2, 19.6);
      if (active) {
        canvas.drawCircle(const Offset(12, 8.1), 4.0, solid);
        canvas.drawPath(
            (Path()
                  ..addPath(shoulders, Offset.zero)
                  ..lineTo(20.2, 19.6)
                  ..lineTo(3.8, 19.6)
                  ..close()),
            solid);
      } else {
        canvas.drawCircle(const Offset(12, 8.1), 3.9, line);
        canvas.drawPath(shoulders, line);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _TabPainter o) =>
      o.index != index || o.active != active || o.color != color;
}
