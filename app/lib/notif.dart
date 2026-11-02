// Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
// SPDX-License-Identifier: MIT
import 'dart:async';
import 'dart:convert';
import 'dart:ui';

import 'package:flutter_background_service/flutter_background_service.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'log.dart';
import 'secrets.dart';
import 'util.dart';

const _channelId = 'stories_messages';
// v2：常驻通知改用新的低重要度频道（Android 频道重要度创建后不可改，换 id 才能生效）
const _serviceChannelId = 'stories_service_v2';

/// 稳定的会话通知 id（跨 isolate 一致；Dart 的 String.hashCode 不稳定，不能直接用）
int _stableId(String key) {
  var h = 0;
  for (final c in key.codeUnits) {
    h = (h * 31 + c) & 0x7fffffff;
  }
  return h & 0xFFFFFF;
}

final FlutterLocalNotificationsPlugin notifPlugin = FlutterLocalNotificationsPlugin();

/// 通知点击回调（由 main.dart 设置）
void Function(String? payload)? notifTapHandler;

bool _notifInited = false;

/// 懒初始化：默认完全不触碰通知组件（规避部分机型首次启动的通知权限弹窗崩溃）；
/// 仅当用户开启"后台通知"时才初始化。
Future<void> ensureNotifInited() async {
  if (_notifInited) return;
  try {
    const android = AndroidInitializationSettings('ic_stat_stories');
    const settings = InitializationSettings(android: android);
    await notifPlugin.initialize(
      settings,
      onDidReceiveNotificationResponse: (resp) {
        notifTapHandler?.call(resp.payload);
      },
    );
    _notifInited = true;
    Log.log('notif', 'notifications initialized (lazy)');
    // 冷启动：App 被杀后点通知进来，需从 launch details 取 payload
    try {
      final details = await notifPlugin.getNotificationAppLaunchDetails();
      if (details?.didNotificationLaunchApp == true) {
        notifTapHandler?.call(details?.notificationResponse?.payload);
      }
    } catch (_) {}
  } catch (e, st) {
    Log.error('notif', 'init failed (ignored): $e', st);
  }
}

Future<void> requestNotifPermission() async {
  try {
    await notifPlugin
        .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission();
  } catch (_) {}
}

Future<void> initBackgroundService() async {
  final service = FlutterBackgroundService();
  await service.configure(
    androidConfiguration: AndroidConfiguration(
      onStart: onServiceStart,
      autoStart: false, // 不在启动时拉起（MIUI 会拦截导致崩溃），由用户开关 + 延迟安全启动
      autoStartOnBoot: false, // 关键：禁止开机/应用更新自启（插件默认 true，是崩溃链的源头）
      isForegroundMode: true,
      notificationChannelId: _serviceChannelId,
      initialNotificationTitle: 'Stories',
      initialNotificationContent: '正在接收消息',
      foregroundServiceNotificationId: 889,
    ),
    iosConfiguration: IosConfiguration(autoStart: false),
  );
}

/// 安全的延迟启动：只允许在 App 前台激活一段时间后调用；
/// 任何异常都被吞掉并且不会使 App 崩溃（插件已打补丁）。
Future<void> startBackgroundServiceSafe() async {
  try {
    // 只有需要后台通知时才触碰通知组件与权限弹窗
    await ensureNotifInited();
    await requestNotifPermission();
    // 频道配置（幂等）：常驻通知=静默折叠；消息通知=高重要度（横幅+声音）
    try {
      final android = notifPlugin
          .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>();
      await android?.createNotificationChannel(const AndroidNotificationChannel(
        _serviceChannelId,
        '消息接收服务',
        description: '保持后台接收消息（常驻通知，静音折叠）',
        importance: Importance.min,
        playSound: false,
        enableVibration: false,
        showBadge: false,
      ));
      await android?.createNotificationChannel(const AndroidNotificationChannel(
        _channelId,
        '消息通知',
        description: '角色发来的消息',
        importance: Importance.high,
        playSound: true,
        enableVibration: true,
      ));
    } catch (_) {}
    final service = FlutterBackgroundService();
    final running = await service.isRunning();
    if (running) {
      Log.log('service', 'already running');
      return;
    }
    await service.startService();
    Log.log('service', 'start requested');
  } catch (e, st) {
    Log.error('service', 'start failed (ignored): $e', st);
  }
  await Log.flush();
}

/// 停止后台服务（关闭开关时调用）
Future<void> stopBackgroundService() async {
  try {
    final service = FlutterBackgroundService();
    if (await service.isRunning()) {
      service.invoke('stopService');
      Log.log('service', 'stop requested');
    }
  } catch (e) {
    Log.error('service', 'stop failed (ignored): $e');
  }
}

@pragma('vm:entry-point')
void onServiceStart(ServiceInstance service) async {
  // 停止指令优先注册：无论后续初始化是否失败，用户关开关都能停掉服务
  try {
    service.on('stopService').listen((_) {
      service.stopSelf();
    });
  } catch (_) {}
  try {
    DartPluginRegistrant.ensureInitialized();
  } catch (_) {
    try {
      service.stopSelf();
    } catch (_) {}
    return;
  }
  try {
  } catch (_) {}
  SharedPreferences prefs;
  try {
    prefs = await SharedPreferences.getInstance();
  } catch (_) {
    try {
      service.stopSelf();
    } catch (_) {}
    return;
  }
  // 后台 isolate 的日志也初始化并指向用户配置的服务器
  try {
    Log.baseUrl = prefs.getString('api_base') ?? Log.baseUrl;
    Log.init();
  } catch (_) {}
  final plugin = FlutterLocalNotificationsPlugin();
  try {
    const android = AndroidInitializationSettings('ic_stat_stories');
    await plugin.initialize(const InitializationSettings(android: android));
  } catch (_) {}
  Log.log('service', 'background service started, base=${prefs.getString('api_base') ?? 'default'}');

  var polling = false;
  var firstPoll = true;
  var needJump = false; // 回后台后的第一次轮询：只推进游标，不补发前台期间的通知
  Timer.periodic(const Duration(seconds: 15), (timer) async {
    if (polling) return; // 防重入：上一轮未结束则跳过
    polling = true;
    try {
      await prefs.reload(); // 跨 isolate 读取最新值（app_foreground/api_base 等）
      final base = prefs.getString('api_base') ?? 'https://your-server.example.com';
      final fg = prefs.getBool('app_foreground') ?? true;
      if (fg) {
        // 前台：页面自身在轮询/刷新，后台服务不发请求（省电省流量）；
        // 回后台后第一次轮询会跳到最新游标，避免补发一堆旧通知
        needJump = true;
        return;
      }
      var since = prefs.getDouble('bg_since') ?? 0;
      // 无游标时只用本地时间做一次"探测请求"，绝不把它持久化
      // （手机时钟快于服务器时，本地时间游标会让该时间窗的推送永久丢失）
      final noCursor = since <= 0;
      if (noCursor) {
        since = DateTime.now().millisecondsSinceEpoch / 1000.0 - 60;
      }
      final r = await http
          .get(Uri.parse('$base/api/poll_all?since=$since'),
              headers: {'X-App-Token': prefs.getString('app_token') ?? appToken})
          .timeout(const Duration(seconds: 20));
      if (r.statusCode < 200 || r.statusCode >= 300) {
        Log.log('service', 'poll_all HTTP ${r.statusCode}');
        return;
      }
      final d = json.decode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;
      final list = (d['messages'] as List?) ?? [];
      final serverNow = (d['now'] as num?)?.toDouble() ?? DateTime.now().millisecondsSinceEpoch / 1000.0;
      // 从未有过游标：直接以服务器时间对齐，跳过历史积压
      if (noCursor) {
        await prefs.setDouble('bg_since', serverNow);
        firstPoll = false;
        Log.log('service', 'no cursor: align cursor to server time');
        return;
      }
      // 首次轮询且积压很久（如长期未开后台）→ 只推进游标，不轰炸历史通知
      if (firstPoll && (serverNow - since) > 1800) {
        await prefs.setDouble('bg_since', serverNow);
        firstPoll = false;
        Log.log('service', 'skip stale backlog, jump cursor');
        return;
      }
      firstPoll = false;
      if (needJump) {
        needJump = false;
        await prefs.setDouble('bg_since', serverNow);
        Log.log('service', 'resume from foreground: jump cursor (skip backlog)');
        return;
      }
      await prefs.setDouble('bg_since', serverNow);
      if (list.isNotEmpty) {
        String? lastTitle;
        String? lastPreview;
        for (final m in list) {
          final mm = (m as Map).cast<String, dynamic>();
          // 通知 id 按"会话"合并：同一角色/群/朋友圈的多条消息只占一条，更新为最新内容（微信式）
          final kindStr = '${mm['kind'] ?? 'text'}';
          final seg = kindStr == 'moment'
              ? 1
              : (kindStr == 'moment_comment' ? 2 : (kindStr == 'group' ? 3 : 0));
          final chatKey = kindStr == 'moment'
              ? 'moments'
              : (kindStr == 'moment_comment'
                  ? 'comments'
                  : (kindStr == 'group' ? 'g${mm['gid']}' : 'c${mm['character_id']}'));
          final id = ((seg + 1) << 24) | _stableId(chatKey);
          final isMoment = kindStr == 'moment' || kindStr == 'moment_comment';
          final title = (mm['name'] ?? 'Stories') as String;
          final preview = cleanMetaTime((mm['preview'] ?? '新消息').toString());
          await plugin.show(
            id,
            title,
            preview,
            NotificationDetails(
              android: AndroidNotificationDetails(
                _channelId,
                '消息通知',
                channelDescription: '角色发来的消息',
                importance: Importance.high,
                priority: Priority.high,
                styleInformation: BigTextStyleInformation(preview),
              ),
            ),
            payload: isMoment
                ? 'moments'
                : (kindStr == 'group' ? 'group:${mm['gid']}' : '${mm['character_id']}'),
          );
          lastTitle = title;
          lastPreview = preview;
        }
        // 常驻通知同步显示最新消息（不再一直显示"保持连接"，点开可进 App）
        try {
          if (service is AndroidServiceInstance && lastPreview != null) {
            await service.setForegroundNotificationInfo(
              title: 'Stories · $lastTitle',
              content: lastPreview,
            );
          }
        } catch (_) {}
        Log.log('service', 'notified ${list.length} new messages');
      }
    } catch (e) {
      Log.error('service', 'poll_all failed: $e');
    } finally {
      polling = false;
    }
    Log.flush();
  });
}
