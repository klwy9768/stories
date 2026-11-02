// Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
// SPDX-License-Identifier: MIT
import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'demo_data.dart';
import 'local_db.dart';
import 'log.dart';
import 'user_profile.dart';
import 'util.dart';
import 'wechat_icons.dart';
import 'notif.dart';
import 'update.dart';
import 'pages/chat_page.dart';
import 'games/games_hub_page.dart';
import 'pages/character_edit_page.dart';
import 'pages/favorites_page.dart';
import 'pages/group_chat_page.dart';
import 'pages/group_create_page.dart';
import 'pages/groups_page.dart';
import 'pages/license_page.dart';
import 'pages/moments_page.dart';
import 'pages/music_page.dart';
import 'pages/my_qr_page.dart';
import 'pages/news_page.dart';
import 'pages/official_page.dart';
import 'pages/profile_page.dart';
import 'pages/recent_chats_page.dart';
import 'pages/roles_manage_page.dart';
import 'pages/search_page.dart';
import 'pages/worlds_page.dart';
import 'miniapps/miniapps_page.dart';

final navKey = GlobalKey<NavigatorState>();

/// 冷启动早期收到的通知点击目标（等界面就绪后统一消费）
int? _pendingChatCid;
int? _pendingGroupId;

/// 通知请求跳转的目标 Tab（1=朋友圈），HomePage 监听
final ValueNotifier<int> tabRequest = ValueNotifier<int>(-1);

void main() {
  runZonedGuarded(() async {
    WidgetsFlutterBinding.ensureInitialized();
    FlutterError.onError = (details) {
      Log.error('flutter', details.exception, details.stack);
      FlutterError.presentError(details);
    };
    PlatformDispatcher.instance.onError = (e, st) {
      Log.error('platform', e, st);
      return true;
    };
    Log.init();
    // ===== 崩溃阶段追踪：下次启动时能知道上次崩在哪一步 =====
    SharedPreferences? prefs;
    try {
      prefs = await SharedPreferences.getInstance();
      final prevStage = prefs.getInt('boot_stage') ?? 0;
      if (prevStage > 0) {
        Log.error('boot', '检测到上次启动在阶段 $prevStage 崩溃！');
        await Log.flush();
      }
      await prefs.setInt('boot_stage', 1);
    } catch (_) {}
    try {
      await Api.loadBase();
      await DemoData.seedIfEmpty();
      Log.baseUrl = apiBase;
      Log.log('boot', 'stage1: api_base=$apiBase');
      // 通知点击 → 打开对应聊天/朋友圈（纯回调，不触碰通知组件）
      notifTapHandler = (payload) {
        if (payload == 'moments') {
          tabRequest.value = 1; // HomePage 监听后切换 Tab（界面未起也能在起来后消费）
          return;
        }
        if (payload != null && payload.startsWith('group:')) {
          final gid = int.tryParse(payload.substring(6)) ?? 0;
          if (gid <= 0) return;
          final navG = navKey.currentState;
          if (navG == null) {
            _pendingGroupId = gid; // 冷启动早期：界面就绪后由 HomePage 消费
            return;
          }
          navG.push(MaterialPageRoute(builder: (_) => GroupChatPage(gid: gid, name: '')));
          return;
        }
        final cid = int.tryParse(payload ?? '');
        if (cid == null) return;
        final nav = navKey.currentState;
        if (nav == null) {
          _pendingChatCid = cid; // 冷启动早期：界面就绪后由 HomePage 消费
          return;
        }
        nav.push(MaterialPageRoute(builder: (_) => ChatPage(cid: cid)));
      };
      if (prefs != null) await prefs.setInt('boot_stage', 2);
    } catch (e, st) {
      Log.error('boot', 'loadBase: $e', st);
    }
    // 注意：本地缓存初始化、通知初始化均已移到界面起来之后（见 HomePage），
    // 启动阶段不再触碰任何本地存储/通知原生组件，保证启动稳定。
    try {
      await initBackgroundService();
      Log.log('boot', 'stage2: service configured (not started)');
      if (prefs != null) await prefs.setInt('boot_stage', 3);
    } catch (e, st) {
      Log.error('boot', 'initBackgroundService: $e', st);
    }
    try {
      if (prefs != null) {
        // 之前开过后台通知：提前初始化通知组件，保证冷启动点通知也能跳转
        if (prefs.getBool('bg_service_enabled') ?? false) {
          try {
            await ensureNotifInited();
          } catch (_) {}
        }
        await prefs.setBool('app_foreground', true);
        // 通知游标不再用手机本地时间初始化：由后台轮询用服务器时间对齐
        // （手机时钟快于服务器时，本地时间游标会让该时间窗的推送永久丢失）
      }
    } catch (_) {}
    unawaited(Log.flush());
    runApp(const StoriesApp());
  }, (e, st) {
    try {
      Log.error('zone', e, st);
    } catch (_) {}
  });
}

ThemeData weChatTheme() {
  return ThemeData(
    brightness: Brightness.light,
    scaffoldBackgroundColor: kWeChatLight,
    colorScheme: const ColorScheme.light(primary: kWeChatTheme),
    appBarTheme: const AppBarTheme(
      backgroundColor: kWeChatLight,
      foregroundColor: Colors.black,
      elevation: 0,
      centerTitle: true,
      titleTextStyle: TextStyle(color: Colors.black, fontSize: 17, fontWeight: FontWeight.w600),
      surfaceTintColor: Colors.transparent,
    ),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith((s) =>
          s.contains(WidgetState.selected) ? Colors.white : Colors.white),
      trackColor: WidgetStateProperty.resolveWith((s) =>
          s.contains(WidgetState.selected) ? kWeChatTheme : const Color(0xFFDFDFDF)),
      trackOutlineColor: WidgetStateProperty.all(Colors.transparent),
    ),
    dividerTheme: const DividerThemeData(color: Color(0xFFE5E5E5), thickness: 0.6, space: 0.6),
  );
}

class StoriesApp extends StatelessWidget {
  const StoriesApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      navigatorKey: navKey,
      title: 'Stories',
      debugShowCheckedModeBanner: false,
      theme: weChatTheme(),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> with WidgetsBindingObserver {
  List<Map<String, dynamic>> _chars = [];
  Map<int, int> _unread = {};
  bool _loading = true;
  bool _newsEnabled = true; // 新闻（看一看）开关（由服务端 bootstrap 下发）
  bool _firstSyncFlushed = false;
  UserProfile? _profile;
  List<Map<String, dynamic>> _groups = [];
  Set<int> _pinned = {};
  Set<int> _hidden = {};
  Map<int, int> _hiddenSeen = {};
  bool _syncing = false;
  Set<int> _manualUnread = {};
  Map<int, String> _groupDrafts = {};
  Timer? _fgTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    tabRequest.addListener(_onTabRequest);
    // 前台停在首页时轻量刷新（30 秒一次，仅微信 Tab 且前台；防止收不到新消息提示）
    _fgTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      if (!mounted) return;
      Api.ensureUrlTicket(); // 票据临近过期才发请求续签（未过期是纯本地判断）
      if (WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed) return;
      if (_tab != 0) return;
      _load();
    });
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      // 进到这里说明 UI 已经正常启动 → 清除崩溃标记
      try {
        final prefs = await SharedPreferences.getInstance();
        await prefs.setInt('boot_stage', 0);
      } catch (_) {}
      Log.log('ui', 'step1: 界面已显示');
      await Log.flush();
      // 本地缓存初始化（纯文件 IO）
      try {
        await LocalDb.init();
        Log.log('ui', 'step2: 本地缓存就绪');
      } catch (e, st) {
        Log.error('ui', '本地缓存初始化失败: $e', st);
      }
      await Log.flush();
      // 后台通知（默认关闭，开启后才触碰通知组件）
      if (!mounted) return;
      _scheduleServiceStart();
      // 冷启动点通知进来的跳转（等界面就绪后消费）
      _consumePendingChat();
      _onTabRequest(); // 冷启动期间已设置的朋友圈跳转（listener 注册前到达）
      // 数据同步
      _load();
      await _loadListPrefs();
      unawaited(cleanupTempCache()); // 清理旧缩略图/中间文件缓存
      // 个人资料（“我”页头像/昵称，避免首次进入显示默认值）
      try {
        final p = await UserProfile.load();
        if (mounted) setState(() => _profile = p);
      } catch (_) {}
      // 检查更新（有新版本弹窗提示）
      if (!mounted) return;
      checkAppUpdate(context);
    });
  }

  void _consumePendingChat() {
    final cid = _pendingChatCid;
    if (cid != null && mounted) {
      _pendingChatCid = null;
      Navigator.push(context, MaterialPageRoute(builder: (_) => ChatPage(cid: cid)));
    }
    final gid = _pendingGroupId;
    if (gid != null && mounted) {
      _pendingGroupId = null;
      Navigator.push(context, MaterialPageRoute(builder: (_) => GroupChatPage(gid: gid, name: '')));
    }
  }

  /// 用户开启「后台通知」后，等 App 稳定可见 8 秒再安全启动服务（避免 MIUI 拦截首启）
  Future<void> _scheduleServiceStart() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final enabled = prefs.getBool('bg_service_enabled') ?? false; // 默认关闭，稳定优先
      if (!enabled) {
        Log.log('service', 'background service disabled by setting');
        return;
      }
      await Future.delayed(const Duration(seconds: 8));
      if (!mounted) return;
      if (WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed) {
        Log.log('service', 'not resumed yet, skip start this time');
        return;
      }
      await startBackgroundServiceSafe();
    } catch (e) {
      Log.error('service', 'schedule start failed: $e');
    }
  }

  void _onTabRequest() {
    if (!mounted) return;
    final t = tabRequest.value;
    if (t < 0) return;
    tabRequest.value = -1; // 消费掉，保证下次同值也能再次触发
    if (t == 1) _openMoments();
  }

  @override
  void dispose() {
    _fgTimer?.cancel();
    tabRequest.removeListener(_onTabRequest);
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) async {
    final prefs = await SharedPreferences.getInstance();
    // inactive（权限弹窗/下拉通知栏）不是真正的后台，避免前台时误发重复通知
    final fg = state == AppLifecycleState.resumed || state == AppLifecycleState.inactive;
    await prefs.setBool('app_foreground', fg);
    if (fg) {
      Api.ensureUrlTicket(); // 回前台检查票据有效期
      // bg_since 只由后台服务用服务器时间推进，避免手机/服务器时钟偏差导致漏通知或重复通知
      // 开关打开但服务没跑起来时，回前台后补启动
      if (prefs.getBool('bg_service_enabled') ?? false) {
        await startBackgroundServiceSafe();
      }
      if (!mounted) return;
      _load();
      checkAppUpdate(context);
    } else if (state == AppLifecycleState.paused) {
      await LocalDb.flush();
      await Log.flush();
    }
  }

  Future<void> _load() async {
    if (_syncing) return; // 防并发重入：多个入口同时触发只跑一轮
    _syncing = true;
    try {
      await _loadInner();
    } finally {
      _syncing = false;
    }
  }

  Future<void> _loadInner() async {
    try {
      await LocalDb.init(); // 惰性初始化（纯文件存储，界面已就绪）
      final local = await LocalDb.allChars();
      final unread = <int, int>{};
      for (final c in local) {
        unread[c['id'] as int] = await LocalDb.unreadCount(c['id'] as int);
      }
      if (mounted) {
        setState(() {
          _chars = local;
          _unread = unread;
          _loading = false;
        });
      }
    } catch (e, st) {
      Log.error('db', 'LocalDb 初始化失败: $e', st);
      if (mounted) setState(() => _loading = false);
    }
    try {
      final b = await Api.bootstrap();
      if (b['news_enabled'] is num) {
        final ne = (b['news_enabled'] as num) != 0;
        if (mounted) {
          setState(() => _newsEnabled = ne);
        } else {
          _newsEnabled = ne;
        }
      }
      final hasChars = b['characters'] is List; // 返回异常时不清本地缓存
      final list = (b['characters'] as List?) ?? [];
      final serverIds = <int>[];
      for (final c in list) {
        final cm = (c as Map).cast<String, dynamic>();
        await LocalDb.upsertChar(cm);
        if (cm['id'] is num) serverIds.add((cm['id'] as num).toInt());
      }
      // 清理服务端已删除的角色（比如在电脑后台删掉了）；仅在拿到完整列表时执行
      if (hasChars) await LocalDb.pruneChars(serverIds);
      // 清理已删除角色的本地状态残留（置顶/不显示/手动未读）
      if (hasChars) {
        final valid = serverIds.toSet();
        final before = _pinned.length + _hidden.length + _manualUnread.length;
        _pinned.removeWhere((e) => !valid.contains(e));
        _hidden.removeWhere((e) => !valid.contains(e));
        _hiddenSeen.removeWhere((k, v) => !valid.contains(k));
        _manualUnread.removeWhere((e) => !valid.contains(e));
        if (before != _pinned.length + _hidden.length + _manualUnread.length) {
          unawaited(_saveListPrefs());
        }
      }
      final local2 = await LocalDb.allChars();
      final unread2 = <int, int>{};
      // 一次批量请求取代"每角色一次请求"（30 秒刷新 × N 角色的请求风暴）
      try {
        final req = <Map<String, int>>[];
        final lrMap = <int, int>{};
        for (final c in local2) {
          final cid2 = c['id'] as int;
          final lr = await LocalDb.lastRead(cid2);
          lrMap[cid2] = lr;
          req.add({'cid': cid2, 'after': lr});
        }
        final r = await Api.unreadBatch(req);
        final items = (r['items'] as Map?)?.cast<String, dynamic>() ?? {};
        for (final c in local2) {
          final cid2 = c['id'] as int;
          final it = (items['$cid2'] as Map?)?.cast<String, dynamic>();
          final count = (it?['count'] as num?)?.toInt() ?? 0;
          final serverLast = (it?['last_id'] as num?)?.toInt() ?? 0;
          if ((lrMap[cid2] ?? 0) <= 0 && serverLast > 0) {
            // 首次安装/本地缓存重建：以服务端最新一条为已读基线，历史消息不刷成未读
            await LocalDb.setLastRead(cid2, serverLast);
            unread2[cid2] = 0;
          } else {
            unread2[cid2] = count;
          }
        }
      } catch (_) {
        for (final c in local2) {
          final cid2 = c['id'] as int;
          unread2[cid2] = await LocalDb.unreadCount(cid2);
        }
      }
      for (final cid in _manualUnread) {
        if ((unread2[cid] ?? 0) < 1) unread2[cid] = 1; // 手动未读优先显示
      }
      if (mounted) {
        setState(() {
          _chars = local2;
          _unread = unread2;
        });
      }
      await _autoUnhide(unread2);
      Log.log('sync', 'bootstrap ok, characters=${local2.length}');
      if (!_firstSyncFlushed) {
        _firstSyncFlushed = true;
        await Log.flush();
      }
    } catch (e, st) {
      Log.error('sync', 'bootstrap failed: $e', st);
      if (!_firstSyncFlushed) {
        _firstSyncFlushed = true;
        await Log.flush();
      }
    }
    try {
      final g = await Api.groups();
      final glist = ((g['groups'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
      if (mounted) setState(() => _groups = glist);
      // 清理已删除群的草稿残留
      final gids = glist.map((e) => (e['id'] as num).toInt()).toSet();
      final stale = _groupDrafts.keys.where((k) => !gids.contains(k)).toList();
      if (stale.isNotEmpty) {
        if (mounted) {
          setState(() {
            for (final k in stale) {
              _groupDrafts.remove(k);
            }
          });
        }
        try {
          final prefs = await SharedPreferences.getInstance();
          for (final k in stale) {
            await prefs.remove('gdraft_$k');
          }
        } catch (_) {}
      }
    } catch (_) {}
    _loadMomentBadge();
  }

  int _tab = 0;
  int _momentUnseen = 0;

  Future<void> _loadMomentBadge() async {
    try {
      final d = await Api.moments(after: 1 << 30);
      if (mounted) setState(() => _momentUnseen = (d['unseen'] as num?)?.toInt() ?? 0);
    } catch (_) {}
  }

  Future<void> _openChat(int cid) async {
    final mid = await LocalDb.maxId(cid);
    await LocalDb.setLastRead(cid, mid);
    if (_manualUnread.remove(cid)) unawaited(_saveListPrefs());
    if (mounted) setState(() => _unread[cid] = 0);
    if (!mounted) return;
    await Navigator.push(context, MaterialPageRoute(builder: (_) => ChatPage(cid: cid)));
    _load();
  }

  Future<void> _openGroup(int gid, String name) async {
    await Navigator.push(
        context, MaterialPageRoute(builder: (_) => GroupChatPage(gid: gid, name: name)));
    await _loadListPrefs(); // 群草稿可能已发送/修改，回首页刷新 [草稿] 显示
    _load();
  }

  Future<void> _newCharacter() async {
    final r = await Navigator.push(
        context, MaterialPageRoute(builder: (_) => const CharacterEditPage()));
    if (r == true) _load();
  }

  Future<void> _openGroups() async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => const GroupsPage()));
    _load();
  }

  void _openWorlds() {
    Navigator.push(context, MaterialPageRoute(builder: (_) => const WorldsPage()));
  }

  void _openMiniApps() {
    Navigator.push(context, MaterialPageRoute(builder: (_) => const MiniAppsPage()));
  }

  Future<void> _openRecentChats() async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => const RecentChatsPage()));
    _load();
  }

  Future<void> _openRolesManage() async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => const RolesManagePage()));
    _load();
  }

  void _openOfficial() {
    Navigator.push(context, MaterialPageRoute(builder: (_) => const OfficialPage()));
  }

  void _openFavorites() {
    Navigator.push(context, MaterialPageRoute(builder: (_) => const FavoritesPage()));
  }

  void _openMusic() {
    Navigator.push(context, MaterialPageRoute(builder: (_) => const MusicPage()));
  }

  void _openGames() {
    Navigator.push(context, MaterialPageRoute(builder: (_) => const GamesHubPage()));
  }

  void _openNews() {
    if (!_newsEnabled) {
      _comingSoon();
      return;
    }
    Navigator.push(context, MaterialPageRoute(builder: (_) => const NewsPage()));
  }

  Future<void> _newGroup() async {
    final ids = await Navigator.push<List<int>>(
        context, MaterialPageRoute(builder: (_) => const GroupCreatePage()));
    if (ids == null || ids.length < 2) return;
    try {
      final r = await Api.groupCreate('', ids);
      if (r['ok'] != true) {
        if (mounted) showToast(context, '${r['error'] ?? '创建失败'}');
        return;
      }
      final gid = (r['id'] as num).toInt();
      if (!mounted) return;
      await Navigator.push(context, MaterialPageRoute(builder: (_) => GroupChatPage(gid: gid)));
      _load();
    } catch (_) {
      if (mounted) showToast(context, '创建失败');
    }
  }

  Widget _groupAvatar(List members) {
    final avatars = members.take(4).toList();
    if (avatars.isEmpty) {
      return Container(
        width: 48,
        height: 48,
        decoration: BoxDecoration(color: const Color(0xFFD6D6D6), borderRadius: BorderRadius.circular(6)),
        child: const Icon(Icons.group, color: Colors.white),
      );
    }
    if (avatars.length == 1) {
      return DefaultAvatar(url: _avatarUrl(avatars[0]['avatar'] as String?), size: 48);
    }
    return ClipRRect(
      borderRadius: BorderRadius.circular(6),
      child: SizedBox(
        width: 48,
        height: 48,
        child: GridView.count(
          crossAxisCount: 2,
          physics: const NeverScrollableScrollPhysics(),
          padding: EdgeInsets.zero,
          mainAxisSpacing: 1,
          crossAxisSpacing: 1,
          children: [
            for (final a in avatars) _groupAvatarCell(a['avatar'] as String?),
          ],
        ),
      ),
    );
  }

  Widget _groupAvatarCell(String? url) {
    final u = _avatarUrl(url);
    if (u == null) {
      return Container(color: const Color(0xFFD6D6D6), child: const Icon(Icons.person, size: 14, color: Colors.white));
    }
    return Image.network(u, fit: BoxFit.cover, cacheWidth: 120,
        errorBuilder: (_, _, _) => Container(color: const Color(0xFFD6D6D6)));
  }

  Widget _groupRow(Map<String, dynamic> g) {
    final members = (g['members'] as List?) ?? [];
    final last = (g['last'] as Map?)?.cast<String, dynamic>();
    final unread = (g['unread'] as num?)?.toInt() ?? 0;
    final draft = (_groupDrafts[(g['id'] as num).toInt()] ?? '').trim();
    final lk = '${last?['kind'] ?? 'text'}';
    final lt = lk == 'voice'
        ? '[语音]'
        : lk == 'sticker'
            ? '[表情]'
            : lk == 'image'
                ? '[图片]'
                : lk == 'video'
                    ? '[视频]'
                    : cleanMetaTime('${last?['content'] ?? ''}');
    final preview = draft.isNotEmpty
        ? '[草稿] $draft'
        : (last == null ? '暂无消息' : (lt.isEmpty ? '（消息）' : lt));
    return InkWell(
      onTap: () => _openGroup((g['id'] as num).toInt(), '${g['name'] ?? ''}'),
      child: Container(
        color: Colors.white,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        child: Row(children: [
          Stack(clipBehavior: Clip.none, children: [
            _groupAvatar(members),
            if (unread > 0)
              Positioned(
                right: -6,
                top: -6,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                  decoration: BoxDecoration(
                    color: const Color(0xFFFA5151),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text(unread > 99 ? '99+' : '$unread',
                      style: const TextStyle(color: Colors.white, fontSize: 11)),
                ),
              ),
          ]),
          const SizedBox(width: 10),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${g['name'] ?? ''}',
                  style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w500),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis),
              const SizedBox(height: 3),
              Text(preview,
                  style: TextStyle(
                      fontSize: 13,
                      color: draft.isNotEmpty ? const Color(0xFFFA5151) : const Color(0xFF999999)),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis),
            ]),
          ),
          const SizedBox(width: 8),
          Text(fmtTime((last?['ts'] as num?)?.toDouble() ?? 0),
              style: const TextStyle(fontSize: 11, color: Color(0xFFB2B2B2))),
        ]),
      ),
    );
  }

  Future<void> _showSettings() async {
    final prefs = await SharedPreferences.getInstance();
    var bgEnabled = prefs.getBool('bg_service_enabled') ?? false;
    var logEnabled = prefs.getBool('log_enabled') ?? true;
    if (!mounted) return;
    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: const Text('设置'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  value: bgEnabled,
                  onChanged: (v) async {
                    setDialogState(() => bgEnabled = v);
                    await prefs.setBool('bg_service_enabled', v);
                    if (v) {
                      await startBackgroundServiceSafe();
                    } else {
                      await stopBackgroundService();
                    }
                  },
                  title: const Text('后台通知'),
                  subtitle: const Text('关闭 App 后仍能收到角色消息（常驻一条"保持连接"通知）',
                      style: TextStyle(fontSize: 12, color: Colors.grey)),
                ),
                const Text(
                  '小米/MIUI 提示：打开本开关前，建议先在系统「设置 → 应用设置 → Stories」中：\n'
                  '① 允许"自启动"  ② 省电策略选"无限制"  ③ 允许通知；\n'
                  '否则系统可能拦截后台连接。若开启后异常，关掉本开关即可恢复正常。',
                  style: TextStyle(fontSize: 12, color: Color(0xFFE6A23C)),
                ),
                const Divider(),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  value: logEnabled,
                  onChanged: (v) async {
                    setDialogState(() => logEnabled = v);
                    Log.enabled = v;
                    await prefs.setBool('log_enabled', v);
                  },
                  title: const Text('日志上报'),
                  subtitle: const Text('帮助定位问题，建议保持开启', style: TextStyle(fontSize: 12, color: Colors.grey)),
                ),
                const Divider(),
                const Text('服务器', style: TextStyle(fontSize: 13, color: Colors.grey)),
                Text(apiBase, style: const TextStyle(fontSize: 13)),
                const Divider(),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('开源许可', style: TextStyle(fontSize: 15)),
                  trailing: const Icon(Icons.chevron_right, size: 18, color: Color(0xFFC7C7C7)),
                  onTap: () {
                    Navigator.pop(ctx);
                    Navigator.push(context,
                        MaterialPageRoute(builder: (_) => const OpenSourceLicensePage()));
                  },
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('关闭')),
          ],
        ),
      ),
    );
  }

  int get _totalUnread => _unread.values.fold(0, (a, b) => a + b);

  void _comingSoon() => showToast(context, '活动未开放');

  PreferredSizeWidget _buildAppBar() {
    if (_tab == 1) {
      return AppBar(
        title: const Text('通讯录'),
        actions: [
          IconButton(icon: const Icon(Icons.person_add_alt, size: 24), onPressed: _newCharacter),
          const SizedBox(width: 4),
        ],
      );
    }
    if (_tab == 2) {
      return AppBar(
        title: const Text('发现'),
        actions: [
          IconButton(
            icon: const WcSearchIcon(size: 24),
            tooltip: '搜索',
            onPressed: () => Navigator.push(
                context, MaterialPageRoute(builder: (_) => SearchPage(chars: _chars))),
          ),
          IconButton(icon: const WcPlusCircleIcon(size: 26), onPressed: _showMainMenu),
          const SizedBox(width: 4),
        ],
      );
    }
    if (_tab == 3) return AppBar(title: const Text(''));
    return AppBar(
      title: Text(_totalUnread > 0 ? 'Stories ($_totalUnread)' : 'Stories'),
      actions: [
        IconButton(
          icon: const WcSearchIcon(size: 24),
          tooltip: '搜索',
          onPressed: () => Navigator.push(
              context, MaterialPageRoute(builder: (_) => SearchPage(chars: _chars))),
        ),
        IconButton(icon: const WcPlusCircleIcon(size: 26), onPressed: _showMainMenu),
        const SizedBox(width: 4),
      ],
    );
  }

  void _showMainMenu() {
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          _menuTile(ctx, Icons.group_add_outlined, '发起群聊', onTap: _newGroup),
          _menuTile(ctx, Icons.person_add_alt, '添加朋友', functional: true),
          _menuTile(ctx, Icons.qr_code_scanner, '扫一扫'),
          _menuTile(ctx, Icons.account_balance_wallet_outlined, '收付款'),
          const SizedBox(height: 6),
        ]),
      ),
    );
  }

  Widget _menuTile(BuildContext ctx, IconData icon, String label, {bool functional = false, VoidCallback? onTap}) {
    return ListTile(
      leading: Icon(icon, color: const Color(0xFF555555)),
      title: Text(label, style: const TextStyle(fontSize: 15)),
      onTap: () async {
        Navigator.pop(ctx);
        if (onTap != null) {
          onTap();
        } else if (functional) {
          final r = await Navigator.push(
              context, MaterialPageRoute(builder: (_) => const CharacterEditPage()));
          if (r == true) _load();
        } else {
          _comingSoon();
        }
      },
    );
  }

  Future<void> _openProfile() async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => const ProfilePage()));
    final p = await UserProfile.load();
    if (mounted) setState(() => _profile = p);
  }

  void _openMyQr() {
    Navigator.push(context, MaterialPageRoute(builder: (_) => const MyQrPage()));
  }

  void _openMoments() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => MomentsHomePage(
          onSeen: () {
            if (mounted) setState(() => _momentUnseen = 0);
          },
        ),
      ),
    ).then((_) {
      if (mounted) _loadMomentBadge();
    });
  }

  void _showChatLongMenu(Map<String, dynamic> c) {
    final cid = c['id'] as int;
    final pinned = _pinned.contains(cid);
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          _menuTile(ctx, Icons.mark_chat_unread_outlined, '标为未读', onTap: () => _markUnread(cid)),
          _menuTile(ctx, Icons.push_pin_outlined, pinned ? '取消置顶' : '置顶该聊天',
              onTap: () => _togglePin(cid)),
          _menuTile(ctx, Icons.visibility_off_outlined, '不显示该聊天', onTap: () => _hideChat(cid)),
          _menuTile(ctx, Icons.delete_outline, '删除该聊天',
              onTap: () => _deleteChat(cid, '${c['name'] ?? ''}')),
          const SizedBox(height: 6),
        ]),
      ),
    );
  }

  Future<void> _loadListPrefs() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final pin = prefs.getStringList('pinned_chats') ?? [];
      final hid = prefs.getStringList('hidden_chats') ?? [];
      final manual = prefs.getStringList('manual_unread') ?? [];
      final gd = <int, String>{};
      for (final k in prefs.getKeys()) {
        if (!k.startsWith('gdraft_')) continue;
        final gid = int.tryParse(k.substring(7));
        final v = prefs.getString(k) ?? '';
        if (gid != null && v.trim().isNotEmpty) gd[gid] = v;
      }
      final seen = <int, int>{};
      final seenJson = prefs.getString('hidden_seen');
      if (seenJson != null && seenJson.isNotEmpty) {
        final m = jsonDecode(seenJson);
        if (m is Map) {
          m.forEach((k, v) {
            final id = int.tryParse('$k');
            if (id != null && v is num) seen[id] = v.toInt();
          });
        }
      }
      if (!mounted) return;
      setState(() {
        _pinned = pin.map(int.tryParse).whereType<int>().toSet();
        _hidden = hid.map(int.tryParse).whereType<int>().toSet();
        _manualUnread = manual.map(int.tryParse).whereType<int>().toSet();
        _groupDrafts = gd;
        _hiddenSeen = seen;
      });
    } catch (_) {}
  }

  Future<void> _saveListPrefs() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList('pinned_chats', _pinned.map((e) => '$e').toList());
      await prefs.setStringList('hidden_chats', _hidden.map((e) => '$e').toList());
      await prefs.setStringList('manual_unread', _manualUnread.map((e) => '$e').toList());
      await prefs.setString('hidden_seen', jsonEncode(_hiddenSeen.map((k, v) => MapEntry('$k', v))));
    } catch (_) {}
  }

  /// 首页可见角色：过滤“不显示”的聊天（有新消息自动恢复显示），置顶排前
  List<Map<String, dynamic>> get _visibleChars {
    final out = <Map<String, dynamic>>[];
    for (final c in _chars) {
      if (c['enabled'] == 0) continue; // 停用的角色不在聊天列表显示（可在「角色管理」重新启用）
      final cid = c['id'] as int;
      if (_hidden.contains(cid) && (_unread[cid] ?? 0) <= (_hiddenSeen[cid] ?? 0)) continue;
      out.add(c);
    }
    if (_pinned.isEmpty) return out;
    final pin = <Map<String, dynamic>>[];
    final rest = <Map<String, dynamic>>[];
    for (final c in out) {
      (_pinned.contains(c['id']) ? pin : rest).add(c);
    }
    return [...pin, ...rest];
  }

  Future<void> _autoUnhide(Map<int, int> unread) async {
    final revived = [
      for (final cid in _hidden)
        if ((unread[cid] ?? 0) > (_hiddenSeen[cid] ?? 0)) cid
    ];
    if (revived.isEmpty) return;
    if (mounted) {
      setState(() {
        _hidden.removeAll(revived);
        for (final c in revived) {
          _hiddenSeen.remove(c);
        }
      });
    } else {
      _hidden.removeAll(revived);
      for (final c in revived) {
        _hiddenSeen.remove(c);
      }
    }
    await _saveListPrefs();
  }

  Future<void> _markUnread(int cid) async {
    try {
      final mid = await LocalDb.maxId(cid);
      if (mid > 0) await LocalDb.setLastRead(cid, mid - 1);
    } catch (_) {}
    _manualUnread.add(cid); // 手动未读：刷新后仍保留，打开聊天后清除
    if (mounted) setState(() => _unread[cid] = 1);
    await _saveListPrefs();
  }

  Future<void> _togglePin(int cid) async {
    setState(() => _pinned.contains(cid) ? _pinned.remove(cid) : _pinned.add(cid));
    await _saveListPrefs();
  }

  Future<void> _hideChat(int cid) async {
    setState(() {
      _hidden.add(cid);
      _hiddenSeen[cid] = _unread[cid] ?? 0;
    });
    await _saveListPrefs();
  }

  Future<void> _deleteChat(int cid, String name) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除该聊天'),
        content: Text('将删除与「$name」的全部聊天记录，且不可恢复。'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('取消', style: TextStyle(color: Colors.black))),
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('删除', style: TextStyle(color: Colors.red))),
        ],
      ),
    );
    if (ok != true) return;
    try {
      final r = await Api.reset(cid);
      final greetId = (r['greeting_id'] as num?)?.toInt() ?? 0;
      try {
        await LocalDb.replaceMessages(cid, []);
        if (greetId > 0) await LocalDb.setLastRead(cid, greetId); // 开场白不算未读
        await LocalDb.setDraft(cid, ''); // 删除聊天同时清草稿
      } catch (_) {}
      if (_manualUnread.remove(cid)) unawaited(_saveListPrefs());
      await _load();
      if (mounted) showToast(context, '已删除');
    } catch (_) {
      if (mounted) showToast(context, '删除失败');
    }
  }

  Widget _entryTile(IconData icon, Color color, String label,
      {bool functional = false, VoidCallback? onTap, Widget? trailing, int badge = 0, Widget? leading}) {
    return ListTile(
      leading: leading ?? Icon(icon, size: 25, color: color),
      title: Row(children: [
        Text(label, style: const TextStyle(fontSize: 16.5)),
        if (badge > 0) ...[
          const SizedBox(width: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
            decoration:
                BoxDecoration(color: const Color(0xFFFA5151), borderRadius: BorderRadius.circular(9)),
            child: Text(badge > 99 ? '99+' : '$badge',
                style: const TextStyle(color: Colors.white, fontSize: 12)),
          ),
        ],
      ]),
      trailing: trailing ?? const Icon(Icons.chevron_right, size: 18, color: Color(0xFFC7C7C7)),
      onTap: onTap ?? (functional ? null : _comingSoon),
    );
  }

  Widget _sectionCard(List<Widget> children) {
    return Container(
        color: Colors.white,
        margin: const EdgeInsets.only(bottom: 10),
        child: Column(children: children));
  }

  Widget _contactsTab() {
    return ListView(children: [
      _sectionCard([
        _entryTile(Icons.person_add_alt, const Color(0xFFFA9D3B), '新的朋友', onTap: _newCharacter),
        _entryTile(Icons.chat_outlined, const Color(0xFF07C160), '仅聊天的朋友', onTap: _openRecentChats),
        _entryTile(Icons.group_outlined, const Color(0xFF07C160), '群聊', onTap: _openGroups),
        _entryTile(Icons.label_outline, const Color(0xFF2782D7), '标签', onTap: _openRolesManage),
        _entryTile(Icons.account_circle_outlined, const Color(0xFF2782D7), '公众号', onTap: _openOfficial),
      ]),
      Container(
        color: Colors.white,
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 10, 16, 6),
            child: Text('联系人', style: TextStyle(fontSize: 13, color: Colors.grey)),
          ),
          ..._chars.map((c) => ListTile(
                leading: DefaultAvatar(url: _avatarUrl(c['avatar'] as String?), size: 40),
                title: Text('${c['name'] ?? ''}', style: const TextStyle(fontSize: 16)),
                onTap: () => _openChat(c['id'] as int),
              )),
          if (_chars.isEmpty)
            const Padding(
              padding: EdgeInsets.all(20),
              child: Center(child: Text('还没有联系人', style: TextStyle(color: Colors.grey))),
            ),
        ]),
      ),
    ]);
  }

  Widget _discoverTab() {
    return ListView(children: [
      _sectionCard([
        _entryTile(Icons.camera_outlined, const Color(0xFF07C160), '朋友圈',
            functional: true,
            onTap: _openMoments,
            badge: _momentUnseen,
            leading: const _MomentsFlower(size: 25)),
      ]),
      _sectionCard([
        _entryTile(Icons.all_inclusive, const Color(0xFFFA8C16), '视频号'),
        _entryTile(Icons.adjust, const Color(0xFFFA5151), '直播'),
      ]),
      _sectionCard([
        _entryTile(Icons.qr_code_scanner, const Color(0xFF2782D7), '扫一扫'),
        _entryTile(Icons.music_note, const Color(0xFFFA5151), '听一听', onTap: _openMusic),
      ]),
      _sectionCard([
        if (_newsEnabled)
          _entryTile(Icons.star_border_rounded, const Color(0xFFF5A623), '看一看', onTap: _openNews),
        _entryTile(Icons.search, const Color(0xFFFA5151), '搜一搜'),
      ]),
      _sectionCard([
        _entryTile(Icons.people_outline, const Color(0xFF2782D7), '附近的人'),
      ]),
      _sectionCard([
        _entryTile(Icons.diamond_outlined, const Color(0xFF07C160), '游戏', onTap: _openGames),
        _entryTile(Icons.public, const Color(0xFF576B95), '世界', onTap: _openWorlds),
      ]),
      _sectionCard([
        _entryTile(Icons.data_usage, const Color(0xFF5B6BF5), '小程序', onTap: _openMiniApps),
      ]),
    ]);
  }

  Widget _mePill({IconData? icon, required String label, VoidCallback? onTap, Widget? trailing, Widget? leading}) {
    return GestureDetector(
      onTap: onTap ?? _comingSoon,
      child: Container(
        height: 32,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        decoration: BoxDecoration(
          border: Border.all(color: const Color(0xFFDDDDDD)),
          borderRadius: BorderRadius.circular(16),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (leading != null) ...[
            leading,
            const SizedBox(width: 6),
          ],
          if (icon != null) ...[
            Icon(icon, size: 16, color: const Color(0xFF333333)),
            const SizedBox(width: 4),
          ],
          Text(label, style: const TextStyle(fontSize: 14, color: Color(0xFF333333))),
          if (trailing != null) ...[
            const SizedBox(width: 6),
            trailing,
          ],
        ]),
      ),
    );
  }

  Widget _friendsPill() {
    final n = _chars.length;
    final avatars = _chars.take(3).toList();
    return _mePill(
      label: n > 0 ? '等$n个朋友' : '朋友圈',
      onTap: _openMoments,
      leading: avatars.isEmpty
          ? null
          : SizedBox(
              width: 22 + (avatars.length - 1) * 13.0,
              height: 22,
              child: Stack(children: [
                for (var i = 0; i < avatars.length; i++)
                  Positioned(
                    left: i * 13.0,
                    child: DefaultAvatar(
                        url: _avatarUrl(avatars[i]['avatar'] as String?), size: 22),
                  ),
              ]),
            ),
      trailing: _totalUnread > 0
          ? Container(
              width: 7,
              height: 7,
              decoration: const BoxDecoration(color: Color(0xFFFA5151), shape: BoxShape.circle),
            )
          : null,
      icon: null,
    );
  }

  Widget _meTab() {
    final p = _profile;
    return ListView(children: [
      GestureDetector(
        onTap: _openProfile,
        child: Container(
          color: Colors.white,
          padding: const EdgeInsets.fromLTRB(16, 18, 14, 16),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            DefaultAvatar(url: (p != null && p.avatar.isNotEmpty) ? p.avatar : null, size: 64),
            const SizedBox(width: 14),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Expanded(
                    child: Text(p?.name ?? '用户',
                        style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w600)),
                  ),
                  GestureDetector(
                    onTap: _openMyQr,
                    child: const Icon(Icons.qr_code, size: 18, color: Color(0xFF888888)),
                  ),
                ]),
                const SizedBox(height: 6),
                Row(children: [
                  Expanded(
                    child: Text('账号：${p?.wxid ?? ''}',
                        style: const TextStyle(fontSize: 14, color: Color(0xFF999999))),
                  ),
                  const Icon(Icons.chevron_right, size: 18, color: Color(0xFFC7C7C7)),
                ]),
                const SizedBox(height: 12),
                Row(children: [
                  _mePill(icon: Icons.add, label: '状态'),
                  const SizedBox(width: 10),
                  _friendsPill(),
                ]),
              ]),
            ),
          ]),
        ),
      ),
      const SizedBox(height: 10),
      _sectionCard([_entryTile(Icons.check_circle_outline, const Color(0xFF07C160), '服务')]),
      _sectionCard([
        _entryTile(Icons.view_in_ar_outlined, const Color(0xFFFA9D3B), '收藏', onTap: _openFavorites),
        _entryTile(Icons.photo_outlined, const Color(0xFF2782D7), '朋友圈',
            functional: true, onTap: _openMoments),
        _entryTile(Icons.filter_none, const Color(0xFF2782D7), '作品'),
        _entryTile(Icons.storefront_outlined, const Color(0xFFFA5151), '小店与卡包'),
        _entryTile(Icons.emoji_emotions_outlined, const Color(0xFFF5A623), '表情'),
      ]),
      _sectionCard([
        _entryTile(Icons.settings_outlined, const Color(0xFF2782D7), '设置',
            functional: true, onTap: _showSettings),
      ]),
    ]);
  }

  Widget _buildOtherTab() {
    switch (_tab) {
      case 1:
        return _contactsTab();
      case 2:
        return _discoverTab();
      default:
        return _meTab();
    }
  }

  @override
  Widget build(BuildContext context) {
    final vis = _visibleChars;
    // 私聊+群聊混排：置顶优先，其余按最近消息时间倒序（对齐微信）
    final entries = <Map<String, dynamic>>[];
    for (final g in _groups) {
      final last = (g['last'] as Map?)?.cast<String, dynamic>();
      entries.add({
        'kind': 'group',
        'g': g,
        'ts': (last?['ts'] as num?)?.toDouble() ?? 0,
        'top': false,
      });
    }
    for (final c in vis) {
      entries.add({
        'kind': 'char',
        'c': c,
        'ts': (c['last_ts'] as num?)?.toDouble() ?? 0,
        'top': _pinned.contains(c['id'] as int),
      });
    }
    entries.sort((a, b) {
      final pa = a['top'] == true ? 1 : 0;
      final pb = b['top'] == true ? 1 : 0;
      if (pa != pb) return pb - pa;
      final ta = (a['ts'] as num?)?.toDouble() ?? 0;
      final tb = (b['ts'] as num?)?.toDouble() ?? 0;
      return tb.compareTo(ta); // 统一按 num 取值，杜绝 int/double 转换崩溃
    });
    return Scaffold(
      appBar: _buildAppBar(),
      body: _tab == 0
          ? RefreshIndicator(
        color: kWeChatTheme,
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : entries.isEmpty
                ? ListView(children: const [
                    SizedBox(height: 160),
                    Center(child: Text('还没有角色', style: TextStyle(color: Colors.grey))),
                    SizedBox(height: 8),
                    Center(
                        child: Text('点右上角 ＋ 新建，或去电脑后台添加',
                            style: TextStyle(color: Colors.grey, fontSize: 13))),
                  ])
                : ListView.separated(
                    physics: const AlwaysScrollableScrollPhysics(),
                    itemCount: entries.length,
                    separatorBuilder: (_, _) => const Divider(indent: 68),
                    itemBuilder: (ctx, i) {
                      final e = entries[i];
                      if (e['kind'] == 'group') {
                        return _groupRow(e['g'] as Map<String, dynamic>);
                      }
                      final c = e['c'] as Map<String, dynamic>;
                      final unread = _unread[c['id']] ?? 0;
                      final draft = ((c['draft'] as String?) ?? '').trim();
                      return InkWell(
                        onTap: () => _openChat(c['id'] as int),
                        onLongPress: () => _showChatLongMenu(c),
                        child: Container(
                          color: Colors.white,
                          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                          child: Row(
                            children: [
                              Stack(
                                clipBehavior: Clip.none,
                                children: [
                                  DefaultAvatar(url: _avatarUrl(c['avatar'] as String?), size: 48),
                                  if (unread > 0)
                                    Positioned(
                                      right: -6,
                                      top: -6,
                                      child: Container(
                                        padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                                        decoration: BoxDecoration(
                                          color: const Color(0xFFFA5151),
                                          borderRadius: BorderRadius.circular(10),
                                        ),
                                        child: Text(
                                          unread > 99 ? '99+' : '$unread',
                                          style: const TextStyle(color: Colors.white, fontSize: 11),
                                        ),
                                      ),
                                    ),
                                ],
                              ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(c['name'] ?? '',
                                        style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w500),
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis),
                                    const SizedBox(height: 3),
                                    Text(
                                        draft.isNotEmpty
                                            ? '[草稿] $draft'
                                            : cleanMetaTime((c['last_content'] ?? '') as String),
                                        style: TextStyle(
                                            fontSize: 13,
                                            color: draft.isNotEmpty
                                                ? const Color(0xFFFA5151)
                                                : const Color(0xFF999999)),
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis),
                                  ],
                                ),
                              ),
                              const SizedBox(width: 8),
                              Text(fmtTime((c['last_ts'] as num?)?.toDouble() ?? 0),
                                  style: const TextStyle(fontSize: 11, color: Color(0xFFB2B2B2))),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
            )
          : _buildOtherTab(),
      bottomNavigationBar: BottomNavigationBar(
        type: BottomNavigationBarType.fixed,
        currentIndex: _tab,
        onTap: (i) {
          setState(() => _tab = i);
          if (i == 0) _load();
        },
        items: [
          BottomNavigationBarItem(
            icon: Stack(clipBehavior: Clip.none, children: [
              const WcTabIcon(index: 0, active: false),
              if (_totalUnread > 0)
                Positioned(
                  right: -8,
                  top: -4,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                    decoration: BoxDecoration(
                        color: const Color(0xFFFA5151), borderRadius: BorderRadius.circular(8)),
                    child: Text(_totalUnread > 99 ? '99+' : '$_totalUnread',
                        style: const TextStyle(color: Colors.white, fontSize: 10)),
                  ),
                ),
            ]),
            activeIcon: const WcTabIcon(index: 0, active: true),
            label: '聊天',
          ),
          const BottomNavigationBarItem(
              icon: WcTabIcon(index: 1, active: false),
              activeIcon: WcTabIcon(index: 1, active: true),
              label: '通讯录'),
          BottomNavigationBarItem(
            icon: Stack(clipBehavior: Clip.none, children: [
              const WcTabIcon(index: 2, active: false),
              if (_momentUnseen > 0)
                Positioned(
                  right: -8,
                  top: -4,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                    decoration: BoxDecoration(
                        color: const Color(0xFFFA5151), borderRadius: BorderRadius.circular(8)),
                    child: Text(_momentUnseen > 99 ? '99+' : '$_momentUnseen',
                        style: const TextStyle(color: Colors.white, fontSize: 10)),
                  ),
                ),
            ]),
            activeIcon: const WcTabIcon(index: 2, active: true),
            label: '发现',
          ),
          const BottomNavigationBarItem(
              icon: WcTabIcon(index: 3, active: false),
              activeIcon: WcTabIcon(index: 3, active: true),
              label: '我'),
        ],
      ),
    );
  }
}

/// 朋友圈独立页（发现 → 朋友圈）：带返回与右上角相机（发动态）
class MomentsHomePage extends StatefulWidget {
  final VoidCallback? onSeen;
  const MomentsHomePage({super.key, this.onSeen});

  @override
  State<MomentsHomePage> createState() => _MomentsHomePageState();
}

class _MomentsHomePageState extends State<MomentsHomePage> {
  final GlobalKey _momentsKey = GlobalKey();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: kWeChatLight,
      body: MomentsPage(key: _momentsKey, onSeen: widget.onSeen),
    );
  }
}

String? _avatarUrl(String? a) => avatarFull(a);

/// 自绘「朋友圈」彩虹花图标（原创几何图形：6 个彩色圆点环形排列）
class _MomentsFlower extends StatelessWidget {
  final double size;
  const _MomentsFlower({this.size = 25});

  @override
  Widget build(BuildContext context) {
    return CustomPaint(size: Size(size, size), painter: _MomentsFlowerPainter());
  }
}

class _MomentsFlowerPainter extends CustomPainter {
  static const _colors = [
    Color(0xFFFA5151),
    Color(0xFFF5A623),
    Color(0xFFE8D44D),
    Color(0xFF07C160),
    Color(0xFF2782D7),
    Color(0xFF9B59B6),
  ];

  @override
  void paint(Canvas canvas, Size size) {
    final dot = size.width * 0.16;
    final ring = size.width * 0.31;
    final c = Offset(size.width / 2, size.height / 2);
    final p = Paint();
    for (var i = 0; i < 6; i++) {
      final a = i * math.pi / 3 - math.pi / 2;
      p.color = _colors[i];
      canvas.drawCircle(Offset(c.dx + ring * math.cos(a), c.dy + ring * math.sin(a)), dot, p);
    }
    p.color = const Color(0xFF07C160);
    canvas.drawCircle(c, size.width * 0.09, p);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
