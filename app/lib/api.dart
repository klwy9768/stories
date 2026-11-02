// Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
// SPDX-License-Identifier: MIT
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'log.dart';
import 'secrets.dart';

String apiBase = 'https://your-server.example.com';

/// 旧版明文地址自动迁移到 HTTPS（2026-09 起全站 HTTPS）
String migrateBase(String v) {
  if (v.startsWith('http://your-server.example.com')) return 'https://your-server.example.com';
  return v;
}

/// URL 票据：图片/媒体/小程序这类"无法携带请求头"的场景用它替代长期令牌（12 小时有效）
String urlTicket = '';
double urlTicketExp = 0;

class Api {

  static Future<void> loadBase() async {
    const envBase = String.fromEnvironment('API_BASE');
    if (envBase.isNotEmpty) {
      apiBase = migrateBase(envBase);
      return;
    }
    final p = await SharedPreferences.getInstance();
    final v = p.getString('api_base');
    if (v != null && v.isNotEmpty) apiBase = migrateBase(v);
    final t = p.getString('app_token');
    if (t != null && t.isNotEmpty) {
      if (t == kOldToken) {
        // 旧令牌已轮换：自动升级为新内置令牌
        appToken = kAppToken;
        try {
          await p.setString('app_token', kAppToken);
        } catch (_) {}
      } else {
        appToken = t;
      }
    }
    await ensureUrlTicket();
  }

  /// 票据是否可用（留 30 分钟余量，避免边界过期）
  static bool get urlTicketUsable =>
      urlTicket.isNotEmpty &&
      urlTicketExp > DateTime.now().millisecondsSinceEpoch / 1000.0 + 1800;

  /// 拉取新的 URL 票据（服务端 12 小时有效）
  static Future<void> refreshUrlTicket() async {
    try {
      final r = await http
          .get(Uri.parse('$apiBase/api/ticket'), headers: {'X-App-Token': appToken})
          .timeout(const Duration(seconds: 20));
      if (r.statusCode < 200 || r.statusCode >= 300) return;
      final d = (json.decode(utf8.decode(r.bodyBytes)) as Map).cast<String, dynamic>();
      final t = '${d['ticket'] ?? ''}'.trim();
      if (t.isEmpty) return;
      urlTicket = t;
      urlTicketExp = (d['exp'] as num?)?.toDouble() ?? 0;
    } catch (e, st) {
      Log.error('api', '获取 URL 票据失败: $e', st);
    }
  }

  /// 需要时自动续签（启动、回前台、定时任务调用）
  static Future<void> ensureUrlTicket() async {
    if (!urlTicketUsable) await refreshUrlTicket();
  }

  static Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        'X-App-Token': appToken,
      };

  static Future<Map<String, dynamic>> _get(String path, {int timeout = 90}) async {
    try {
      final r = await http
          .get(Uri.parse('$apiBase$path'), headers: {'X-App-Token': appToken})
          .timeout(Duration(seconds: timeout));
      if (r.statusCode < 200 || r.statusCode >= 300) {
        throw HttpException('GET $path -> HTTP ${r.statusCode}', uri: Uri.parse('$apiBase$path'));
      }
      return (json.decode(utf8.decode(r.bodyBytes)) as Map).cast<String, dynamic>();
    } catch (e, st) {
      Log.error('api', 'GET $path 失败: $e', st);
      rethrow;
    }
  }

  static Future<Map<String, dynamic>> _post(String path, [Map? body]) async {
    try {
      final r = await http
          .post(Uri.parse('$apiBase$path'), headers: _headers, body: json.encode(body ?? {}))
          .timeout(const Duration(seconds: 90));
      if (r.statusCode < 200 || r.statusCode >= 300) {
        throw HttpException('POST $path -> HTTP ${r.statusCode}', uri: Uri.parse('$apiBase$path'));
      }
      return (json.decode(utf8.decode(r.bodyBytes)) as Map).cast<String, dynamic>();
    } catch (e, st) {
      Log.error('api', 'POST $path 失败: $e', st);
      rethrow;
    }
  }

  static Future<Map<String, dynamic>> _multipart(String path, Map<String, String> fields,
      {File? file, String fileField = 'file'}) async {
    try {
      final req = http.MultipartRequest('POST', Uri.parse('$apiBase$path'));
      req.headers['X-App-Token'] = appToken;
      req.fields.addAll(fields);
      if (file != null) {
        req.files.add(await http.MultipartFile.fromPath(fileField, file.path));
      }
      final streamed = await req.send().timeout(const Duration(seconds: 180));
      final r = await http.Response.fromStream(streamed);
      if (r.statusCode < 200 || r.statusCode >= 300) {
        throw HttpException('UPLOAD $path -> HTTP ${r.statusCode}', uri: Uri.parse('$apiBase$path'));
      }
      return (json.decode(utf8.decode(r.bodyBytes)) as Map).cast<String, dynamic>();
    } catch (e, st) {
      Log.error('api', 'UPLOAD $path 失败: $e', st);
      rethrow;
    }
  }

  static Future<Map<String, dynamic>> bootstrap() => _get('/api/bootstrap');

  static Future<Map<String, dynamic>> chatMessages(int cid, int after, {bool wait = false}) =>
      _get('/api/chat/messages?character_id=$cid&after_id=$after${wait ? '&wait=true' : ''}',
          timeout: wait ? 40 : 90);

  static Future<Map<String, dynamic>> unread(int cid, int after) =>
      _get('/api/chat/unread?character_id=$cid&after_id=$after', timeout: 30);

  /// 批量未读：一次请求取回全部角色（替代每角色一次请求的 N+1 风暴）
  static Future<Map<String, dynamic>> unreadBatch(List<Map<String, int>> items) =>
      _post('/api/chat/unread_batch', {'items': items});

  // ===== 回忆（长期记忆）=====

  static Future<Map<String, dynamic>> memoryList(int cid,
          {String q = '', int limit = 50, int offset = 0}) =>
      _get('/api/memory/list?cid=$cid&limit=$limit&offset=$offset&q=${Uri.encodeComponent(q)}');

  static Future<Map<String, dynamic>> memoryUpdate(int cid, int mid,
      {int? pin, int? dormant, String? content}) {
    final body = <String, dynamic>{'cid': cid, 'mid': mid};
    if (pin != null) body['pin'] = pin;
    if (dormant != null) body['dormant'] = dormant;
    if (content != null) body['content'] = content;
    return _post('/api/memory/update', body);
  }

  static Future<Map<String, dynamic>> memoryDelete(int cid, int mid) =>
      _post('/api/memory/delete', {'cid': cid, 'mid': mid});

  static Future<Map<String, dynamic>> send(int cid, String text) =>
      _post('/api/chat/send', {'character_id': cid, 'content': text});

  static Future<Map<String, dynamic>> sendImage(int cid, File file, {String caption = ''}) =>
      _multipart('/api/chat/send_image', {'character_id': '$cid', 'caption': caption}, file: file);

  static Future<Map<String, dynamic>> sendVideo(int cid, File file, {String caption = ''}) =>
      _multipart('/api/chat/send_video', {'character_id': '$cid', 'caption': caption}, file: file);

  static Future<Map<String, dynamic>> voiceMessage(int cid, File file,
          {String mode = 'send', double duration = 0}) =>
      _multipart('/api/chat/voice',
          {'character_id': '$cid', 'mode': mode, 'duration': duration.toStringAsFixed(1)},
          file: file);

  // ===== 群聊 =====

  static Future<Map<String, dynamic>> groups() => _get('/api/groups', timeout: 30);

  static Future<Map<String, dynamic>> miniapps() => _get('/api/miniapps', timeout: 30);

  static Future<Map<String, dynamic>> worlds() => _get('/api/worlds', timeout: 30);

  static Future<Map<String, dynamic>> groupCreate(String name, List<int> cids) =>
      _post('/api/group/create', {'name': name, 'cids': cids});

  static Future<Map<String, dynamic>> groupMessages(int gid, int after) =>
      _get('/api/group/messages?gid=$gid&after_id=$after', timeout: 60);

  static Future<Map<String, dynamic>> groupSend(int gid, String text) =>
      _post('/api/group/send', {'gid': gid, 'content': text});

  static Future<Map<String, dynamic>> groupSendImage(int gid, File file) =>
      _multipart('/api/group/send_image', {'gid': '$gid'}, file: file);

  static Future<Map<String, dynamic>> groupSendVideo(int gid, File file) =>
      _multipart('/api/group/send_video', {'gid': '$gid'}, file: file);

  static Future<Map<String, dynamic>> groupSettings(int gid,
      {String? name, bool? auto, List<int>? cids, bool delete = false}) {
    final m = <String, dynamic>{'gid': gid, 'delete': delete};
    if (name != null) m['name'] = name;
    if (auto != null) m['auto'] = auto;
    if (cids != null) m['cids'] = cids;
    return _post('/api/group/settings', m);
  }

  // ===== 收藏（网盘）/ 音乐 =====

  static Future<Map<String, dynamic>> _multiUpload(String path, List<File> files,
      {String field = 'files'}) async {
    try {
      final req = http.MultipartRequest('POST', Uri.parse('$apiBase$path'));
      req.headers['X-App-Token'] = appToken;
      for (final f in files) {
        req.files.add(await http.MultipartFile.fromPath(field, f.path));
      }
      final streamed = await req.send().timeout(const Duration(seconds: 600));
      final r = await http.Response.fromStream(streamed);
      if (r.statusCode < 200 || r.statusCode >= 300) {
        throw HttpException('UPLOAD $path -> HTTP ${r.statusCode}', uri: Uri.parse('$apiBase$path'));
      }
      return (json.decode(utf8.decode(r.bodyBytes)) as Map).cast<String, dynamic>();
    } catch (e, st) {
      Log.error('api', 'UPLOAD $path 失败: $e', st);
      rethrow;
    }
  }

  static Future<Map<String, dynamic>> favorites() => _get('/api/favorites', timeout: 60);

  static Future<Map<String, dynamic>> favoriteUpload(List<File> files) =>
      _multiUpload('/api/favorites/upload', files);

  static Future<Map<String, dynamic>> favoriteDelete(int id) =>
      _post('/api/favorites/delete', {'id': id});

  static Future<Map<String, dynamic>> favoriteRename(int id, String name) =>
      _post('/api/favorites/rename', {'id': id, 'name': name});

  static Future<Map<String, dynamic>> music() => _get('/api/music', timeout: 60);

  static Future<Map<String, dynamic>> musicUpload(List<File> files) =>
      _multiUpload('/api/music/upload', files);

  static Future<Map<String, dynamic>> musicDelete(int id) => _post('/api/music/delete', {'id': id});

  // ===== 看一看（每日新闻） =====

  static Future<Map<String, dynamic>> news({int days = 2}) =>
      _get('/api/news?days=$days', timeout: 60);

  static Future<Map<String, dynamic>> newsRefresh() => _post('/api/news/refresh', {});

  // ===== 朋友圈 =====

  static Future<Map<String, dynamic>> moments({int after = 0}) =>
      _get('/api/moments?after_id=$after', timeout: 60);

  static Future<Map<String, dynamic>> momentLike(int id) =>
      _post('/api/moments/like', {'id': id});

  static Future<Map<String, dynamic>> momentComment(int id, String content, {int replyTo = 0}) =>
      _post('/api/moments/comment', {'id': id, 'content': content, 'reply_to': replyTo});

  static Future<Map<String, dynamic>> momentSeen() => _post('/api/moments/seen', {});

  static Future<Map<String, dynamic>> momentPost(String content, List<File> files,
      {List<int> visibleTo = const [], List<int> hiddenFrom = const []}) async {
    try {
      final req = http.MultipartRequest('POST', Uri.parse('$apiBase/api/moments/post'));
      req.headers['X-App-Token'] = appToken;
      req.fields['content'] = content;
      if (visibleTo.isNotEmpty) req.fields['visible_to'] = json.encode(visibleTo);
      if (hiddenFrom.isNotEmpty) req.fields['hidden_from'] = json.encode(hiddenFrom);
      for (final f in files.take(9)) {
        req.files.add(await http.MultipartFile.fromPath('files', f.path));
      }
      final streamed = await req.send().timeout(const Duration(seconds: 180));
      final r = await http.Response.fromStream(streamed);
      if (r.statusCode < 200 || r.statusCode >= 300) {
        throw HttpException('POST /api/moments/post -> HTTP ${r.statusCode}',
            uri: Uri.parse('$apiBase/api/moments/post'));
      }
      return (json.decode(utf8.decode(r.bodyBytes)) as Map).cast<String, dynamic>();
    } catch (e, st) {
      Log.error('api', 'POST /api/moments/post 失败: $e', st);
      rethrow;
    }
  }

  static Future<Map<String, dynamic>> cheat(int cid, String cmd) =>
      _post('/api/cheat', {'character_id': cid, 'command': cmd});

  static Future<Map<String, dynamic>> rewind(int cid, int msgId) =>
      _post('/api/chat/rewind', {'character_id': cid, 'msg_id': msgId});

  static Future<Map<String, dynamic>> chatSettings(int cid,
      {bool? photo,
      bool? voice,
      bool? sticker,
      bool? video,
      bool? imgDesc,
      bool? voiceDesc,
      bool? wake,
      bool? reminder,
      double? reminderTarget,
      bool? momentsAuto,
      bool? momentsReact,
      String? background}) {
    final m = <String, dynamic>{'character_id': cid};
    if (photo != null) m['photo_enabled'] = photo;
    if (voice != null) m['voice_enabled'] = voice;
    if (sticker != null) m['sticker_enabled'] = sticker;
    if (video != null) m['video_enabled'] = video;
    if (imgDesc != null) m['img_desc_enabled'] = imgDesc;
    if (voiceDesc != null) m['voice_desc_enabled'] = voiceDesc;
    if (wake != null) m['wake_enabled'] = wake;
    if (reminder != null) m['reminder_enabled'] = reminder;
    if (reminderTarget != null) m['reminder_target'] = reminderTarget;
    if (momentsAuto != null) m['moments_auto'] = momentsAuto;
    if (momentsReact != null) m['moments_react'] = momentsReact;
    if (background != null) m['background'] = background;
    return _post('/api/chat/settings', m);
  }

  static Future<Map<String, dynamic>> reset(int cid) => _post('/api/chat/reset', {'id': cid});

  static Future<Map<String, dynamic>> backgrounds() => _get('/api/backgrounds');

  static Future<Map<String, dynamic>> kbList(int cid) => _get('/api/kb/list?character_id=$cid');

  static Future<Map<String, dynamic>> kbUpload(int cid, File file) =>
      _multipart('/api/kb/upload', {'scope': 'character', 'character_id': '$cid'}, file: file);

  static Future<Map<String, dynamic>> characterGet(int cid) => _get('/api/characters/$cid');

  static Future<Map<String, dynamic>> characterSave(Map<String, dynamic> body) =>
      _post('/api/character/save', body);

  static Future<Map<String, dynamic>> avatarUpload(int cid, File file) =>
      _multipart('/api/character/avatar', {'character_id': '$cid'}, file: file);

  static Future<Map<String, dynamic>> deleteChar(int cid) => _post('/api/character/delete', {'id': cid});

  static Future<Map<String, dynamic>> pollAll(double since) => _get('/api/poll_all?since=$since', timeout: 30);

  static Future<Map<String, dynamic>> appVersion() => _get('/api/app_version', timeout: 20);

  /// 修改服务器地址（写入本地配置，无需重新打包；下次启动完全生效）
  static Future<void> saveBase(String base) async {
    var v = base.trim();
    if (v.isEmpty) return;
    if (!v.startsWith('http://') && !v.startsWith('https://')) v = 'https://$v'; // 缺协议自动补全（优先 HTTPS）
    v = migrateBase(v);
    while (v.endsWith('/')) {
      v = v.substring(0, v.length - 1); // 去末尾斜杠，避免拼出 //api/...
    }
    if (v.isEmpty) return;
    apiBase = v;
    Log.baseUrl = v; // 日志上报同步切到新服务器
    final p = await SharedPreferences.getInstance();
    await p.setString('api_base', v);
  }

  /// 修改访问令牌（写入本地配置；部分请求立即生效）
  static Future<void> saveToken(String token) async {
    final v = token.trim();
    if (v.isEmpty) return;
    appToken = v;
    final p = await SharedPreferences.getInstance();
    await p.setString('app_token', v);
  }
}
