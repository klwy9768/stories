import 'dart:io';

import 'package:convert/convert.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'log.dart';
import 'util.dart';
import 'version.dart';

bool _checking = false;

/// 版本号解析：取主.次.修订（忽略 +build 与后缀）
List<int> _verNums(String v) {
  final core = v.trim().split('+').first;
  final parts = core.split(RegExp(r'[.\-_]'));
  final out = <int>[];
  for (final p in parts) {
    final digits = p.replaceAll(RegExp(r'[^0-9]'), '');
    if (digits.isEmpty) break;
    out.add(int.tryParse(digits) ?? 0);
  }
  while (out.length < 3) {
    out.add(0);
  }
  return out;
}

/// 服务器版本是否比当前新（原实现只做字符串相等判断：服务器回滚或格式差异都会误报更新）
bool _isNewerVersion(String server, String current) {
  final a = _verNums(server);
  final b = _verNums(current);
  for (var i = 0; i < 3; i++) {
    if (a[i] != b[i]) return a[i] > b[i];
  }
  return false;
}

/// 检查更新：有新版本弹窗提示 → 下载 → 调起系统安装（覆盖安装）
Future<void> checkAppUpdate(BuildContext context, {bool manual = false}) async {
  if (_checking) return;
  _checking = true;
  try {
    final d = await Api.appVersion();
    final ver = '${d['version'] ?? ''}'.trim();
    if (ver.isEmpty || !_isNewerVersion(ver, kAppVersion)) {
      if (manual && context.mounted) showToast(context, '已是最新版本 v$kAppVersion');
      return;
    }
    final prefs = await SharedPreferences.getInstance();
    final today = DateTime.now().toIso8601String().substring(0, 10);
    if (!manual && prefs.getString('skip_update_version') == ver && prefs.getString('skip_update_day') == today) {
      return; // 今天已点过"稍后"
    }
    if (!context.mounted) return;
    final notes = '${d['notes'] ?? ''}'.trim();
    final go = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('发现新版本 v$ver'),
        content: Text('当前版本 v$kAppVersion\n\n'
            '${notes.isNotEmpty ? notes : '建议更新到最新版本'}\n\n'
            '点击「立即更新」将自动下载并调起安装（覆盖安装，聊天记录不受影响）。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('稍后'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('立即更新'),
          ),
        ],
      ),
    );
    if (go == true) {
      if (context.mounted) await _downloadAndInstall(context, d);
    } else if (!manual) {
      await prefs.setString('skip_update_version', ver);
      await prefs.setString('skip_update_day', today);
    }
  } catch (e, st) {
    Log.error('update', '检查更新失败: $e', st);
    if (manual && context.mounted) showToast(context, '检查更新失败，请稍后再试');
  } finally {
    _checking = false;
  }
}

Future<void> _downloadAndInstall(BuildContext context, Map<String, dynamic> info) async {
  final progress = ValueNotifier<double>(0);
  var dialogClosed = false;
  showDialog(
    context: context,
    barrierDismissible: false,
    builder: (ctx) => AlertDialog(
      title: const Text('正在下载更新…'),
      content: ValueListenableBuilder<double>(
        valueListenable: progress,
        builder: (_, v, _) => Column(mainAxisSize: MainAxisSize.min, children: [
          LinearProgressIndicator(value: v > 0 ? v : null),
          const SizedBox(height: 10),
          Text(v > 0 ? '${(v * 100).toStringAsFixed(0)}%' : '准备中…'),
        ]),
      ),
    ),
  );
  http.Client? client;
  try {
    final url = fullUrl('${info['apk'] ?? '/uploads/Stories.apk'}');
    final dir = await getApplicationDocumentsDirectory();
    final f = File('${dir.path}${Platform.pathSeparator}Stories_update.apk');
    if (await f.exists()) {
      try {
        await f.delete();
      } catch (_) {}
    }
    client = http.Client();
    final resp = await client.send(http.Request('GET', Uri.parse(url)));
    if (resp.statusCode != 200) {
      throw Exception('HTTP ${resp.statusCode}');
    }
    final total = resp.contentLength ?? (info['size'] as num?)?.toInt() ?? 0;
    final sink = f.openWrite();
    var got = 0;
    await for (final chunk in resp.stream) {
      got += chunk.length;
      sink.add(chunk);
      if (total > 0) progress.value = (got / total).clamp(0.0, 1.0);
    }
    await sink.close();
    if (context.mounted) {
      try {
        Navigator.of(context, rootNavigator: true).pop();
        dialogClosed = true;
      } catch (_) {}
    }
    final expect = (info['size'] as num?)?.toInt() ?? 0;
    if (expect > 0) {
      final st = await f.stat();
      if (st.size != expect) {
        if (context.mounted) showToast(context, '下载不完整，请重试');
        return;
      }
    }
    // 完整性校验：sha256 优先、md5 兜底；两者都没有时直接中止安装
    // （原实现"缺失即跳过校验"，等于给被篡改的安装包开后门）
    final wantSha = '${info['sha256'] ?? ''}'.trim().toLowerCase();
    final wantMd5 = '${info['md5'] ?? ''}'.trim().toLowerCase();
    if (wantSha.isEmpty && wantMd5.isEmpty) {
      try {
        await f.delete();
      } catch (_) {}
      if (context.mounted) showToast(context, '安装包缺少校验信息，已中止安装');
      return;
    }
    final bool bad;
    if (wantSha.isNotEmpty) {
      final output = AccumulatorSink<Digest>();
      final input = sha256.startChunkedConversion(output);
      await for (final chunk in f.openRead()) {
        input.add(chunk);
      }
      input.close();
      final got = (output.events.isEmpty ? '' : output.events.single.toString()).toLowerCase();
      bad = got != wantSha;
    } else {
      final got = (await md5.bind(f.openRead()).first).toString().toLowerCase();
      bad = got != wantMd5;
    }
    if (bad) {
      try {
        await f.delete(); // 校验失败清理坏文件，避免残留占空间
      } catch (_) {}
      if (context.mounted) showToast(context, '安装包校验失败，请重试');
      return;
    }
    final r = await OpenFilex.open(f.path);
    if (r.type != ResultType.done && context.mounted) {
      showToast(context, '请允许「安装未知应用」后重试（${r.message}）');
    }
  } catch (e, st) {
    Log.error('update', '下载更新失败: $e', st);
    if (!dialogClosed && context.mounted) {
      try {
        Navigator.of(context, rootNavigator: true).pop();
      } catch (_) {}
    }
    if (context.mounted) showToast(context, '下载失败，请检查网络后重试');
  } finally {
    client?.close();
    progress.dispose();
  }
}
