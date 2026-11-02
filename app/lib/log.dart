// Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
// SPDX-License-Identifier: MIT
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'secrets.dart';

/// App 端日志：本地打印 + 批量上报到服务器（后台服务/主进程通用）
class Log {
  static String baseUrl = 'https://your-server.example.com';
  static final List<Map<String, dynamic>> _buf = [];
  static Timer? _timer;
  static String _device = '';
  static bool enabled = true;

  static void init() {
    try {
      _device = '${Platform.operatingSystem} ${Platform.operatingSystemVersion}';
    } catch (_) {
      _device = 'unknown';
    }
    _timer ??= Timer.periodic(const Duration(seconds: 8), (_) => flush());
    // 心跳：每分钟上报一次存活，用于定位偶发崩溃发生的时间点
    Timer.periodic(const Duration(seconds: 60), (_) {
      if (enabled) log('heartbeat', 'alive');
    });
    _loadPref();
    log('lifecycle', '===== app start =====');
    log('lifecycle', 'Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>');
  }

  static Future<void> _loadPref() async {
    try {
      final p = await SharedPreferences.getInstance();
      enabled = p.getBool('log_enabled') ?? true;
    } catch (_) {}
  }

  static void log(String tag, String message, {String level = 'INFO'}) {
    try {
      debugPrint('[$level][$tag] $message');
    } catch (_) {}
    if (!enabled) return;
    try {
      if (_buf.length >= 300) _buf.removeAt(0);
      _buf.add({
        'level': level,
        'tag': tag,
        'message': message.length > 1800 ? message.substring(0, 1800) : message,
        'ts': DateTime.now().millisecondsSinceEpoch / 1000.0,
        'device': _device,
      });
    } catch (_) {}
  }

  static void error(String tag, Object e, [StackTrace? st]) {
    final msg = st != null ? '$e\n$st' : '$e';
    log(tag, msg, level: 'ERROR');
  }

  static Future<void> flush() async {
    if (_buf.isEmpty) return;
    final batch = List<Map<String, dynamic>>.from(_buf);
    _buf.clear();
    try {
      await http
          .post(Uri.parse('$baseUrl/api/app_log'),
              headers: {'Content-Type': 'application/json', 'X-App-Token': appToken},
              body: json.encode({'logs': batch}))
          .timeout(const Duration(seconds: 15));
    } catch (_) {
      // 上报失败直接丢弃，避免堆积拖慢 App
    }
  }
}
