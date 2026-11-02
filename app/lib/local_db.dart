// Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
// SPDX-License-Identifier: MIT
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// 本地缓存：纯文件 JSON 存储（替代 sqflite）
/// 说明：sqflite 在 Android 16 / MIUI 上发生过进程级原生崩溃，
/// 本实现只使用 Dart 文件 IO + path_provider，稳定性优先；服务器始终是唯一真源。
class LocalDb {
  /// 缓存格式版本：升级后旧格式缓存自动清空，稍后从服务器按新格式全量重下
  /// v3：清理历史消息里误带的 [时间] 标注（服务器已清理，本地缓存需重新拉取）
  static const int cacheVer = 3;
  static const int maxMsgsPerChat = 800; // 单聊/群聊本地缓存条数上限（防无限膨胀）
  static Map<String, dynamic> _data = {
    'cache_ver': cacheVer,
    'chars': <String, dynamic>{},
    'msgs': <String, dynamic>{},
    'gmsgs': <String, dynamic>{},
  };
  static bool _loaded = false;
  static Future<void>? _initFuture;
  static Timer? _saveTimer;
  static File? _file;
  static int _rev = 0;        // 数据变更版本号（每次变更 +1）
  static int _savedRev = 0;   // 已成功落盘的版本号（相同则跳过重写）
  // 草稿单独存小文件：打字时不会重写整个消息缓存（性能）
  static File? _draftFile;
  static Map<String, dynamic> _drafts = <String, dynamic>{};
  static Timer? _draftSaveTimer;

  static Future<void> init() {
    if (_loaded) return Future.value();
    return _initFuture ??= _doInit();
  }

  static Future<void> _doInit() async {
    try {
      final dir = await getApplicationDocumentsDirectory();
      _file = File('${dir.path}${Platform.pathSeparator}stories_cache.json');
      _draftFile = File('${dir.path}${Platform.pathSeparator}stories_drafts.json');
      try {
        if (await _draftFile!.exists()) {
          final dd = json.decode(await _draftFile!.readAsString());
          if (dd is Map) _drafts = dd.cast<String, dynamic>();
        }
      } catch (_) {
        _drafts = <String, dynamic>{}; // 草稿文件损坏：重建，不影响消息缓存
      }
      if (await _file!.exists()) {
        final txt = await _file!.readAsString();
        final d = json.decode(txt);
        if (d is Map) {
          _data = {
            'chars': (d['chars'] as Map?)?.cast<String, dynamic>() ?? <String, dynamic>{},
            'msgs': (d['msgs'] as Map?)?.cast<String, dynamic>() ?? <String, dynamic>{},
            'gmsgs': (d['gmsgs'] as Map?)?.cast<String, dynamic>() ?? <String, dynamic>{},
          };
          final ver = (d['cache_ver'] as num?)?.toInt() ?? 0;
          if (ver != cacheVer) {
            _data['msgs'] = <String, dynamic>{}; // 旧格式消息缓存作废，等全量重下
            _data['gmsgs'] = <String, dynamic>{};
          }
          _data['cache_ver'] = cacheVer;
          if (ver != cacheVer) _scheduleSave();
        }
      }
    } catch (_) {
      _data = {
        'cache_ver': cacheVer,
        'chars': <String, dynamic>{},
        'msgs': <String, dynamic>{},
        'gmsgs': <String, dynamic>{},
      };
      try {
        await _file?.delete(); // 缓存损坏：删掉重建，避免本次会话永不落盘
      } catch (_) {}
    } finally {
      _loaded = true;
      _initFuture = null;
    }
  }

  static Future<void> _ensure() async {
    if (!_loaded) await init();
  }

  static Map<String, dynamic> get _chars => (_data['chars'] as Map).cast<String, dynamic>();
  static Map<String, dynamic> get _msgsAll => (_data['msgs'] as Map).cast<String, dynamic>();
  static Map<String, dynamic> get _gmsgsAll => (_data['gmsgs'] as Map).cast<String, dynamic>();

  static List<Map<String, dynamic>> _msgsOf(int cid) {
    final v = _msgsAll['$cid'];
    if (v is List) {
      return v.map((e) => (e as Map).cast<String, dynamic>()).toList();
    }
    return [];
  }

  /// 不复制整表的只读视图（cast 是视图，不产生新列表）
  static List<Map<String, dynamic>> _msgsRaw(int cid) {
    final v = _msgsAll['$cid'];
    if (v is List) {
      return v.cast<Map<String, dynamic>>();
    }
    return const <Map<String, dynamic>>[];
  }

  /// 搜索本地缓存消息：直接扫描内存原始数据（不复制整表），只返回命中的精简结果。
  /// 说明：原实现把每个角色的全部消息复制进页面内存再逐条扫描，长会话下等于双份内存。
  static Future<List<Map<String, dynamic>>> searchMessages(
      String query, List<int> cids, {int limit = 60}) async {
    await _ensure();
    final q = query.trim().toLowerCase();
    if (q.isEmpty) return [];
    final out = <Map<String, dynamic>>[];
    for (final cid in cids) {
      for (final m in _msgsRaw(cid)) {
        final kind = '${m['kind'] ?? ''}';
        if (kind == 'photo' || kind == 'video') continue; // 照片/视频的 content 是内部提示词，不外泄
        final content = '${m['content'] ?? ''}';
        if (content.isEmpty) continue;
        if (content.toLowerCase().contains(q)) {
          out.add({'cid': cid, 'content': content, 'ts': m['ts'] ?? 0, 'id': m['id']});
        }
      }
    }
    out.sort((a, b) => ((b['ts'] ?? 0) as num).compareTo((a['ts'] ?? 0) as num));
    return out.length > limit ? out.sublist(0, limit) : out;
  }

  static void _setMsgs(int cid, List<Map<String, dynamic>> msgs) {
    _msgsAll['$cid'] = msgs;
  }

  static List<Map<String, dynamic>> _gmsgsOf(int gid) {
    final v = _gmsgsAll['$gid'];
    if (v is List) {
      return v.map((e) => (e as Map).cast<String, dynamic>()).toList();
    }
    return [];
  }

  static void _trimMsgs(List<Map<String, dynamic>> list, {int? max}) {
    final cap = max ?? maxMsgsPerChat;
    if (list.length > cap) list.removeRange(0, list.length - cap);
  }

  static void _scheduleSave() {
    _rev++; // 数据变更计数：_saveNow 只在这之后才真正重写文件
    _saveTimer?.cancel();
    // 活跃聊天时消息连续到达：合并窗口拉长到 1.2s，避免频繁全量序列化
    _saveTimer = Timer(const Duration(milliseconds: 1200), _saveNow);
  }

  static Future<void> _saveNow() async {
    try {
      final f = _file;
      if (f == null) return;
      final rev = _rev;
      if (rev == _savedRev) return; // 无变更：不做无意义的全量重写
      final tmp = File('${f.path}.tmp');
      await tmp.writeAsString(json.encode(_data), flush: true);
      await tmp.rename(f.path);
      _savedRev = rev;
    } catch (_) {}
  }

  static void _scheduleDraftSave() {
    _draftSaveTimer?.cancel();
    _draftSaveTimer = Timer(const Duration(milliseconds: 700), _saveDraftsNow);
  }

  static Future<void> _saveDraftsNow() async {
    try {
      final f = _draftFile;
      if (f == null) return;
      final tmp = File('${f.path}.tmp');
      await tmp.writeAsString(jsonEncode(_drafts), flush: true);
      await tmp.rename(f.path);
    } catch (_) {}
  }

  /// 立即落盘（App 退到后台时调用）
  static Future<void> flush() async {
    await _ensure(); // 防止在 init 完成前用空数据覆盖缓存文件
    _saveTimer?.cancel();
    _draftSaveTimer?.cancel();
    await Future.wait([_saveNow(), _saveDraftsNow()]);
  }

  // ===== 角色 =====

  static Future<void> upsertChar(Map<String, dynamic> c) async {
    await _ensure();
    final id = (c['id'] as num?)?.toInt();
    if (id == null) return;
    final last = c['last'] as Map?;
    final chat = (c['chat'] as Map?) ?? {};
    final exist = _chars['$id'];
    _chars['$id'] = {
      'id': id,
      'name': c['name'] ?? '',
      'avatar': c['avatar'] ?? '',
      'description': c['description'] ?? '',
      'greeting': c['greeting'] ?? '',
      'enabled': (c['enabled'] as num?)?.toInt() ?? 1,
      'json': jsonEncode({'chat': chat}),
      // 服务端 last 为空（清空/无消息）时同步清掉旧预览，避免首页残留
      'last_content': last == null ? '' : (last['content'] ?? ''),
      'last_ts': last == null ? 0.0 : ((last['ts'] ?? 0) as num).toDouble(),
      'last_read_id': exist is Map ? (exist['last_read_id'] ?? 0) : 0,
    };
    _scheduleSave();
  }

  static Future<List<Map<String, dynamic>>> allChars() async {
    await _ensure();
    final list = _chars.values.map((e) {
      final m = (e as Map).cast<String, dynamic>();
      m['draft'] = _drafts['${m['id']}'] ?? ''; // 草稿单独存储，读取时合并
      return m;
    }).toList();
    list.sort((a, b) {
      final ta = (a['last_ts'] as num?)?.toDouble() ?? 0;
      final tb = (b['last_ts'] as num?)?.toDouble() ?? 0;
      if (tb != ta) return tb.compareTo(ta);
      return ((b['id'] as num?)?.toInt() ?? 0).compareTo((a['id'] as num?)?.toInt() ?? 0);
    });
    return list;
  }

  static Future<Map<String, dynamic>?> getChar(int id) async {
    await _ensure();
    final v = _chars['$id'];
    if (v is Map) {
      final m = v.cast<String, dynamic>();
      m['draft'] = _drafts['$id'] ?? '';
      return m;
    }
    return null;
  }

  static Future<void> updateChatJson(int cid, Map<String, dynamic> chat) async {
    await _ensure();
    final v = _chars['$cid'];
    if (v is Map) {
      v['json'] = jsonEncode({'chat': chat});
      _scheduleSave();
    }
  }

  static Future<void> setCharEnabled(int cid, bool enabled) async {
    await _ensure();
    final v = _chars['$cid'];
    if (v is Map) {
      v['enabled'] = enabled ? 1 : 0;
      _scheduleSave();
    }
  }

  static Future<void> setLastRead(int cid, int msgId) async {
    await _ensure();
    final v = _chars['$cid'];
    if (v is Map) {
      v['last_read_id'] = msgId;
      _scheduleSave();
    }
  }

  static Future<int> unreadCount(int cid) async {
    await _ensure();
    final v = _chars['$cid'];
    final lastRead = v is Map ? ((v['last_read_id'] as num?)?.toInt() ?? 0) : 0;
    var n = 0;
    for (final m in _msgsOf(cid)) {
      final id = (m['id'] as num?)?.toInt() ?? 0;
      if (m['role'] == 'character' && id > lastRead) n++;
    }
    return n;
  }

  static Future<int> lastRead(int cid) async {
    await _ensure();
    final v = _chars['$cid'];
    return v is Map ? ((v['last_read_id'] as num?)?.toInt() ?? 0) : 0;
  }

  // ===== 输入草稿（未发送的内容，退出后保留；单独小文件存储） =====

  static Future<void> setDraft(int cid, String draft) async {
    await _ensure();
    final d = draft.length > 2000 ? draft.substring(0, 2000) : draft;
    final key = '$cid';
    if (d.trim().isEmpty) {
      if (_drafts.remove(key) != null) _scheduleDraftSave();
    } else if (_drafts[key] != d) {
      _drafts[key] = d;
      _scheduleDraftSave();
    }
  }

  static Future<String> draft(int cid) async {
    await _ensure();
    return '${_drafts['$cid'] ?? ''}';
  }

  // ===== 消息 =====

  static Future<void> insertMessages(int cid, List<Map<String, dynamic>> msgs) async {
    if (msgs.isEmpty) return;
    await _ensure();
    final list = _msgsOf(cid);
    final ids = list.map((m) => (m['id'] as num).toInt()).toSet();
    for (final m in msgs) {
      final id = (m['id'] as num?)?.toInt();
      if (id == null || ids.contains(id)) continue;
      list.add({
        'id': id,
        'role': m['role'],
        'kind': m['kind'] ?? 'text',
        'content': m['content'] ?? '',
        'media_url': m['media_url'] ?? '',
        'duration': (m['duration'] ?? 0).toDouble(),
        'ts': ((m['ts'] ?? 0) as num).toDouble(),
      });
      ids.add(id);
    }
    list.sort((a, b) => ((a['id'] as num).toInt()).compareTo((b['id'] as num).toInt()));
    _trimMsgs(list);
    _setMsgs(cid, list);
    final last = msgs.last;
    final v = _chars['$cid'];
    if (v is Map) {
      v['last_content'] = _preview(last);
      v['last_ts'] = ((last['ts'] ?? 0) as num).toDouble();
    }
    _scheduleSave();
  }

  static Future<List<Map<String, dynamic>>> messagesFor(int cid) async {
    await _ensure();
    return _msgsOf(cid);
  }

  static Future<int> maxId(int cid) async {
    await _ensure();
    var m = 0;
    for (final x in _msgsOf(cid)) {
      final id = (x['id'] as num?)?.toInt() ?? 0;
      if (id > m) m = id;
    }
    return m;
  }

  static Future<int> countLocal(int cid) async {
    await _ensure();
    return _msgsOf(cid).length;
  }

  static Future<void> replaceMessages(int cid, List<Map<String, dynamic>> msgs) async {
    await _ensure();
    final list = msgs
        .map((m) => {
              'id': (m['id'] as num?)?.toInt() ?? 0,
              'role': m['role'],
              'kind': m['kind'] ?? 'text',
              'content': m['content'] ?? '',
              'media_url': m['media_url'] ?? '',
              'duration': (m['duration'] ?? 0).toDouble(),
              'ts': ((m['ts'] ?? 0) as num).toDouble(),
            })
        .toList();
    list.sort((a, b) => ((a['id'] as num).toInt()).compareTo((b['id'] as num).toInt()));
    _trimMsgs(list);
    _setMsgs(cid, list);
    final v = _chars['$cid'];
    if (v is Map) {
      if (list.isNotEmpty) {
        final last = list.last;
        v['last_content'] = _preview(last);
        v['last_ts'] = ((last['ts'] ?? 0) as num).toDouble();
      } else {
        v['last_content'] = '';
        v['last_ts'] = 0;
      }
    }
    _scheduleSave();
  }

  static Future<void> clearMessages(int cid) async {
    await _ensure();
    _setMsgs(cid, []);
    final v = _chars['$cid'];
    if (v is Map) {
      v['last_content'] = '';
      v['last_ts'] = 0;
    }
    _scheduleSave();
  }

  static Future<void> deleteChar(int cid) async {
    await _ensure();
    _chars.remove('$cid');
    _msgsAll.remove('$cid');
    if (_drafts.remove('$cid') != null) _scheduleDraftSave();
    _scheduleSave();
  }

  static Future<void> pruneChars(List<int> keepIds) async {
    await _ensure();
    final keep = keepIds.map((e) => '$e').toSet();
    // 注意：不清理 _drafts / _msgsAll —— 服务端列表缺失不代表角色被删除（可能是停用），
    // 本地数据（草稿与聊天历史）先保留；真正删除角色走 deleteChar。
    final toRemove = _chars.keys.where((k) => !keep.contains(k)).toList();
    for (final k in toRemove) {
      _chars.remove(k);
    }
    if (toRemove.isNotEmpty) _scheduleSave();
  }

  static String _preview(Map<String, dynamic> m) {
    final kind = m['kind'] ?? 'text';
    if (kind == 'voice') return '[语音]';
    if (kind == 'photo') return '[照片]';
    if (kind == 'video') return '[视频]';
    if (kind == 'sticker') return '[表情]';
    if (kind == 'image') return '[图片]';
    return (m['content'] ?? '') as String;
  }

  // ===== 群聊消息缓存（无服务器时的离线可读） =====

  static Future<void> insertGroupMessages(int gid, List<Map<String, dynamic>> msgs) async {
    if (msgs.isEmpty) return;
    await _ensure();
    final list = _gmsgsOf(gid);
    final ids = list.map((m) => (m['id'] as num).toInt()).toSet();
    for (final m in msgs) {
      final id = (m['id'] as num?)?.toInt();
      if (id == null || ids.contains(id)) continue;
      list.add({
        'id': id,
        'role': m['role'],
        'kind': m['kind'] ?? 'text',
        'content': m['content'] ?? '',
        'media_url': m['media_url'] ?? '',
        'cid': (m['cid'] as num?)?.toInt() ?? 0,
        'name': m['name'] ?? '',
        'ts': ((m['ts'] ?? 0) as num).toDouble(),
      });
      ids.add(id);
    }
    list.sort((a, b) => ((a['id'] as num).toInt()).compareTo((b['id'] as num).toInt()));
    _trimMsgs(list, max: 400);
    _gmsgsAll['$gid'] = list;
    _scheduleSave();
  }

  static Future<void> replaceGroupMessages(int gid, List<Map<String, dynamic>> msgs) async {
    await _ensure();
    final list = msgs
        .map((m) => {
              'id': (m['id'] as num?)?.toInt() ?? 0,
              'role': m['role'],
              'kind': m['kind'] ?? 'text',
              'content': m['content'] ?? '',
              'media_url': m['media_url'] ?? '',
              'cid': (m['cid'] as num?)?.toInt() ?? 0,
              'name': m['name'] ?? '',
              'ts': ((m['ts'] ?? 0) as num).toDouble(),
            })
        .toList();
    list.sort((a, b) => ((a['id'] as num).toInt()).compareTo((b['id'] as num).toInt()));
    _trimMsgs(list, max: 400);
    _gmsgsAll['$gid'] = list;
    _scheduleSave();
  }

  static Future<List<Map<String, dynamic>>> groupMessagesFor(int gid) async {
    await _ensure();
    return _gmsgsOf(gid);
  }

  static Future<int> maxGroupId(int gid) async {
    await _ensure();
    var m = 0;
    for (final x in _gmsgsOf(gid)) {
      final id = (x['id'] as num?)?.toInt() ?? 0;
      if (id > m) m = id;
    }
    return m;
  }

  static Future<void> deleteGroup(int gid) async {
    await _ensure();
    if (_gmsgsAll.remove('$gid') != null) _scheduleSave();
  }
}
