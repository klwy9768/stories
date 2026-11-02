import 'dart:io';

import 'package:flutter/material.dart';

import '../api.dart';
import '../local_db.dart';
import '../log.dart';
import '../pick.dart';
import '../user_profile.dart';
import '../util.dart';
import 'character_moments_page.dart';

const List<String> kMomentVideoExts = ['.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v'];

bool isMomentVideo(String path) {
  final lower = path.toLowerCase();
  return kMomentVideoExts.any(lower.endsWith);
}

/// 朋友圈（仿微信）：动态流 + 点赞/评论 + 发动态
class MomentsPage extends StatefulWidget {
  final VoidCallback? onSeen;
  const MomentsPage({super.key, this.onSeen});

  @override
  State<MomentsPage> createState() => _MomentsPageState();
}

class _MomentsPageState extends State<MomentsPage> {
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;
  int _unseen = 0;
  final Set<int> _likingIds = {};
  String? _userAvatar;
  String _userName = '用户';

  @override
  void initState() {
    super.initState();
    UserProfile.load().then((p) {
      if (mounted) {
        setState(() {
          _userAvatar = p.avatar.isEmpty ? null : p.avatar;
          _userName = p.name;
        });
      }
    });
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await Api.moments();
      final list = ((d['moments'] as List?) ?? [])
          .map((e) => (e as Map).cast<String, dynamic>())
          .toList();
      final unseen = (d['unseen'] as num?)?.toInt() ?? 0;
      if (mounted) {
        setState(() {
          _items = list;
          _loading = false;
          _unseen = unseen; // 以服务端为准（归 0 时本地角标同步消失）
        });
      }
      if (unseen > 0) {
        await Api.momentSeen();
        widget.onSeen?.call();
      }
    } catch (e, st) {
      Log.error('moments', '加载失败: $e', st);
      if (mounted) setState(() => _loading = false);
    }
  }

  void reload() => _load();

  void _showActions(Map<String, dynamic> m) {
    final id = m['id'] as int;
    final liked = m['liked_by_user'] == true;
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: Icon(liked ? Icons.favorite : Icons.favorite_border,
                color: const Color(0xFF576B95)),
            title: Text(liked ? '取消赞' : '赞', style: const TextStyle(fontSize: 15)),
            onTap: () {
              Navigator.pop(ctx);
              _like(id);
            },
          ),
          ListTile(
            leading: const Icon(Icons.mode_comment_outlined, color: Color(0xFF576B95)),
            title: const Text('评论', style: TextStyle(fontSize: 15)),
            onTap: () {
              Navigator.pop(ctx);
              _comment(id);
            },
          ),
          const SizedBox(height: 4),
        ]),
      ),
    );
  }

  /// 封面：全宽 4:3，返回/相机按钮悬浮其上，头像与昵称压在封面右下角
  Widget _coverHeader() {
    final w = MediaQuery.of(context).size.width;
    final coverH = w * 0.75;
    return SizedBox(
      height: coverH + 15,
      child: Stack(clipBehavior: Clip.none, children: [
        Positioned(
          top: 0,
          left: 0,
          right: 0,
          height: coverH,
          child: Container(
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [Color(0xFF5B6B7D), Color(0xFF2F3B49)],
              ),
            ),
          ),
        ),
        Positioned(
          top: 0,
          left: 0,
          right: 0,
          child: SafeArea(
            bottom: false,
            child: Row(children: [
              IconButton(
                icon: const Icon(Icons.arrow_back_ios_new, color: Colors.white, size: 20),
                onPressed: () => Navigator.maybePop(context),
              ),
              const Spacer(),
              IconButton(
                icon: const Icon(Icons.photo_camera_outlined, color: Colors.white, size: 25),
                tooltip: '发朋友圈',
                onPressed: _compose,
              ),
              const SizedBox(width: 6),
            ]),
          ),
        ),
        Positioned(
          right: 12,
          bottom: 0,
          child: DecoratedBox(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(6),
              boxShadow: const [BoxShadow(color: Colors.black26, blurRadius: 6)],
            ),
            child: DefaultAvatar(url: _userAvatar, size: 52),
          ),
        ),
        Positioned(
          right: 78,
          bottom: 32,
          child: Text(_userName,
              style: const TextStyle(
                  color: Colors.white,
                  fontSize: 17,
                  fontWeight: FontWeight.w600,
                  shadows: [Shadow(color: Colors.black45, blurRadius: 5)])),
        ),
      ]),
    );
  }

  Future<void> _like(int id) async {
    if (_likingIds.contains(id)) return; // 防重入（仅针对同一条动态）
    _likingIds.add(id);
    try {
      final r = await Api.momentLike(id);
      // 本地更新，避免整表重拉
      if (mounted) {
        setState(() {
          for (final m in _items) {
            if (m['id'] == id) m['liked_by_user'] = r['liked'] == true;
          }
        });
      }
    } catch (_) {
    } finally {
      _likingIds.remove(id);
    }
  }

  Future<void> _comment(int id, {int replyTo = 0, String replyName = ''}) async {
    final ctrl = TextEditingController();
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom),
        child: Container(
          color: Colors.white,
          padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
          child: Row(children: [
            Expanded(
              child: TextField(
                controller: ctrl,
                autofocus: true,
                decoration: InputDecoration(
                    hintText: replyName.isEmpty ? '评论…' : '回复 $replyName：',
                    border: InputBorder.none),
              ),
            ),
            TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('发送', style: TextStyle(color: kWeChatTheme)),
            ),
          ]),
        ),
      ),
    );
    if (ok == true && ctrl.text.trim().isNotEmpty) {
      final text = ctrl.text.trim();
      try {
        await Api.momentComment(id, text, replyTo: replyTo);
        // 本地追加评论，避免整表重拉
        if (mounted) {
          setState(() {
            for (final m in _items) {
              if (m['id'] == id) {
                final list = ((m['comments'] as List?) ?? []).toList();
                list.add({
                  'id': 0,
                  'role': 'user',
                  'content': text,
                  'ts': DateTime.now().millisecondsSinceEpoch / 1000.0,
                  'reply_to': replyTo,
                  'character_id': 0,
                  'name': '我',
                  'avatar': '',
                });
                m['comments'] = list;
              }
            }
          });
        }
      } catch (_) {}
    }
    ctrl.dispose();
  }

  void _openCharacterMoments(Map<String, dynamic> m) {
    final cid = (m['character_id'] as num?)?.toInt() ?? 0;
    if (cid <= 0) return;
    Navigator.push(
        context,
        MaterialPageRoute(
            builder: (_) => CharacterMomentsPage(
                  cid: cid,
                  name: '${m['name'] ?? ''}',
                  avatar: m['avatar'] as String?,
                )));
  }

  Future<void> _compose() async {
    final r = await Navigator.push(
        context, MaterialPageRoute(builder: (_) => const MomentComposePage()));
    if (r == true) _load();
  }

  void _playMomentVideo(String url) {
    showVideoViewer(context, fullUrl(url));
  }

  String _time(num? ts) {
    if (ts == null || ts <= 0) return '';
    final dt = DateTime.fromMillisecondsSinceEpoch((ts * 1000).toInt());
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final day = DateTime(dt.year, dt.month, dt.day);
    final diff = today.difference(day).inDays;
    if (diff <= 0) {
      final d = now.difference(dt);
      if (d.inMinutes < 1) return '刚刚';
      if (d.inHours < 1) return '${d.inMinutes}分钟前';
      return '${d.inHours}小时前';
    }
    if (diff == 1) return '昨天';
    if (diff < 7) return '$diff天前';
    if (now.year == dt.year) return '${dt.month}月${dt.day}日';
    return '${dt.year}年${dt.month}月${dt.day}日';
  }

  Widget _post(Map<String, dynamic> m) {
    final isUser = (m['author'] ?? 'character') == 'user';
    final images = ((m['images'] as List?) ?? []).cast<dynamic>().map((e) => '$e').toList();
    final likedByUser = m['liked_by_user'] == true;
    final likedByChar = m['liked_by_character'] == true;
    final comments = ((m['comments'] as List?) ?? [])
        .map((e) => (e as Map).cast<String, dynamic>())
        .toList();
    final likeNames = ((m['like_names'] as List?) ?? []).map((e) => '$e').toList();
    final cname = '${m['name'] ?? ''}';
    final single = images.length == 1;
    final sz = single ? 170.0 : 86.0;
    final names = [
      if (likedByUser) '我',
      ...likeNames,
      if (likedByChar && !likeNames.contains(cname)) cname,
    ];
    final commentById = <int, Map<String, dynamic>>{
      for (final c in comments)
        if (c['id'] is num) (c['id'] as num).toInt(): c,
    };
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 10),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        GestureDetector(
          onTap: isUser ? null : () => _openCharacterMoments(m),
          child: DefaultAvatar(
              url: isUser ? _userAvatar : fullUrl(m['avatar'] as String?), size: 44),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            GestureDetector(
              onTap: isUser ? null : () => _openCharacterMoments(m),
              child: Text(isUser ? '我' : cname,
                  style: const TextStyle(
                      color: Color(0xFF576B95), fontWeight: FontWeight.w600, fontSize: 15.5)),
            ),
            const SizedBox(height: 3),
            if (('${m['content'] ?? ''}').isNotEmpty)
              Text(cleanMetaTime('${m['content'] ?? ''}'), style: const TextStyle(fontSize: 15.5, height: 1.4)),
            if (images.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Wrap(
                  spacing: 4,
                  runSpacing: 4,
                  children: images.map((u) {
                    if (isMomentVideo(u)) {
                      return GestureDetector(
                        onTap: () => _playMomentVideo(u),
                        child: SizedBox(
                          width: sz,
                          height: sz,
                          child: Stack(alignment: Alignment.center, children: [
                            VideoThumb(
                                url: fullUrl(u),
                                width: sz,
                                height: sz,
                                radius: BorderRadius.circular(4)),
                            Container(
                              width: sz * 0.32,
                              height: sz * 0.32,
                              decoration: const BoxDecoration(
                                  color: Colors.black45, shape: BoxShape.circle),
                              child: Icon(Icons.play_arrow, color: Colors.white, size: sz * 0.22),
                            ),
                          ]),
                        ),
                      );
                    }
                    return GestureDetector(
                      onTap: () => showImageViewer(context, fullUrl(u)),
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(4),
                        child: Image.network(fullUrl(u),
                            width: sz,
                            height: sz,
                            fit: BoxFit.cover,
                            cacheWidth: 400,
                            errorBuilder: (_, _, _) =>
                                Container(width: sz, height: sz, color: Colors.black12)),
                      ),
                    );
                  }).toList(),
                ),
              ),
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Row(children: [
                Text(_time(m['ts'] as num?),
                    style: const TextStyle(fontSize: 12.5, color: Color(0xFF999999))),
                const Spacer(),
                GestureDetector(
                  onTap: () => _showActions(m),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 2),
                    decoration: BoxDecoration(
                        color: const Color(0xFFF0F2F5), borderRadius: BorderRadius.circular(4)),
                    child: const Text('··',
                        style: TextStyle(
                            fontSize: 15,
                            color: Color(0xFF576B95),
                            fontWeight: FontWeight.bold,
                            height: 1.1)),
                  ),
                ),
              ]),
            ),
            if (names.isNotEmpty || comments.isNotEmpty)
              Container(
                margin: const EdgeInsets.only(top: 6),
                width: double.infinity,
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                decoration: BoxDecoration(
                    color: const Color(0xFFF7F7F7), borderRadius: BorderRadius.circular(4)),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (names.isNotEmpty)
                      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        const Padding(
                          padding: EdgeInsets.only(top: 2),
                          child: Icon(Icons.favorite_border, size: 14, color: Color(0xFF576B95)),
                        ),
                        const SizedBox(width: 5),
                        Expanded(
                          child: Text(names.join('、'),
                              style: const TextStyle(
                                  fontSize: 13.5, color: Color(0xFF576B95), height: 1.4)),
                        ),
                      ]),
                    ...comments.map((c) {
                      final cidc = (c['id'] as num?)?.toInt() ?? 0;
                      final who = c['role'] == 'user'
                          ? '我'
                          : ('${c['name'] ?? ''}'.isNotEmpty ? c['name'] : cname);
                      var prefix = '$who：';
                      final rt = (c['reply_to'] as num?)?.toInt() ?? 0;
                      if (rt > 0) {
                        final t = commentById[rt];
                        if (t != null) {
                          final tw = t['role'] == 'user'
                              ? '我'
                              : ('${t['name'] ?? ''}'.isNotEmpty ? t['name'] : cname);
                          prefix = '$who 回复 $tw：';
                        }
                      }
                      return GestureDetector(
                        onTap: () => _comment(m['id'] as int, replyTo: cidc, replyName: who),
                        child: Padding(
                          padding: const EdgeInsets.symmetric(vertical: 3),
                          child: Text.rich(TextSpan(children: [
                            TextSpan(
                                text: prefix,
                                style: const TextStyle(color: Color(0xFF576B95), fontSize: 13.5)),
                            TextSpan(
                                text: cleanMetaTime('${c['content'] ?? ''}'),
                                style: const TextStyle(fontSize: 13.5, height: 1.4)),
                          ])),
                        ),
                      );
                    }),
                  ],
                ),
              ),
          ]),
        ),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: const Color(0xFFEDEDED),
      child: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              color: kWeChatTheme,
              onRefresh: _load,
              child: ListView.builder(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: EdgeInsets.zero,
                itemCount: 1 + (_unseen > 0 ? 1 : 0) + _items.length + (_items.isEmpty ? 1 : 0),
                itemBuilder: (ctx, i) {
                  if (i == 0) return _coverHeader();
                  var k = i - 1;
                  if (_unseen > 0) {
                    if (k == 0) {
                      return Center(
                        child: GestureDetector(
                          onTap: () => setState(() => _unseen = 0),
                          child: Container(
                            margin: const EdgeInsets.only(top: 46, bottom: 2),
                            padding: const EdgeInsets.fromLTRB(8, 6, 14, 6),
                            decoration: BoxDecoration(
                                color: const Color(0xCC4C4C4C),
                                borderRadius: BorderRadius.circular(6)),
                            child: Row(mainAxisSize: MainAxisSize.min, children: [
                              Container(
                                width: 22,
                                height: 22,
                                decoration: BoxDecoration(
                                    color: const Color(0xFFDDDDDD),
                                    borderRadius: BorderRadius.circular(4)),
                                child: const Icon(Icons.photo, size: 14, color: Colors.white),
                              ),
                              const SizedBox(width: 8),
                              Text('$_unseen条新消息',
                                  style: const TextStyle(color: Colors.white, fontSize: 14)),
                            ]),
                          ),
                        ),
                      );
                    }
                    k -= 1;
                  }
                  if (k < _items.length) {
                    // 倒序展示（最新在上）；懒加载，不一次性构建全部动态
                    final idx = _items.length - 1 - k;
                    return Column(children: [_post(_items[idx]), const SizedBox(height: 8)]);
                  }
                  return const Padding(
                    padding: EdgeInsets.all(30),
                    child: Center(
                        child: Text('还没有动态', style: TextStyle(color: Colors.grey))),
                  );
                },
              ),
            ),
    );
  }
}

/// 发朋友圈：文字 + 最多 9 图
class MomentComposePage extends StatefulWidget {
  const MomentComposePage({super.key});

  @override
  State<MomentComposePage> createState() => _MomentComposePageState();
}

class _MomentComposePageState extends State<MomentComposePage> {
  final _text = TextEditingController();
  final List<File> _files = [];
  bool _sending = false;
  String _visMode = 'all'; // all=所有人 / only=仅选中可见 / except=选中不可见
  final Set<int> _visSel = {};
  List<Map<String, dynamic>> _chars = [];

  @override
  void initState() {
    super.initState();
    _loadChars();
  }

  Future<void> _loadChars() async {
    try {
      final cs = await LocalDb.allChars();
      if (mounted) setState(() => _chars = cs);
    } catch (_) {}
  }

  String _visSummary() {
    if (_visMode == 'all' || _chars.isEmpty) return '所有人可见';
    final names = _chars
        .where((c) => _visSel.contains((c['id'] as num?)?.toInt()))
        .map((c) => '${c['name']}')
        .join('、');
    if (names.isEmpty) {
      return _visMode == 'only' ? '仅选中的人可见（尚未选择）' : '选中的人不可见（尚未选择）';
    }
    return _visMode == 'only' ? '仅 $names 可见' : '$names 不可见';
  }

  Future<void> _pickVisibility() async {
    if (_chars.isEmpty) {
      showToast(context, '还没有角色，先创建一个吧');
      return;
    }
    var mode = _visMode;
    final sel = Set<int>.from(_visSel);
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => StatefulBuilder(builder: (ctx2, setSt) {
        final modes = [('all', '所有人可见'), ('only', '仅选中的人可见'), ('except', '选中的人不可见')];
        return SafeArea(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 8, 0),
              child: Row(children: [
                const Text('谁可以看', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const Spacer(),
                TextButton(onPressed: () => Navigator.pop(ctx2), child: const Text('完成')),
              ]),
            ),
            for (final m in modes)
              ListTile(
                dense: true,
                leading: Icon(
                  mode == m.$1 ? Icons.radio_button_checked : Icons.radio_button_off,
                  color: mode == m.$1 ? kWeChatTheme : Colors.grey,
                ),
                title: Text(m.$2, style: const TextStyle(fontSize: 14)),
                onTap: () => setSt(() => mode = m.$1),
              ),
            if (mode != 'all')
              Flexible(
                child: ListView(
                  shrinkWrap: true,
                  children: _chars.map((c) {
                    final cid = (c['id'] as num?)?.toInt() ?? 0;
                    return CheckboxListTile(
                      dense: true,
                      value: sel.contains(cid),
                      title: Text('${c['name'] ?? ''}', style: const TextStyle(fontSize: 14)),
                      onChanged: (v) => setSt(() {
                        if (v == true) {
                          sel.add(cid);
                        } else {
                          sel.remove(cid);
                        }
                      }),
                    );
                  }).toList(),
                ),
              ),
          ]),
        );
      }),
    );
    if (mounted) {
      setState(() {
        _visMode = mode;
        _visSel
          ..clear()
          ..addAll(sel);
      });
    }
  }

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _pick() async {
    final choice = await showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: const Icon(Icons.photo_camera_outlined),
            title: const Text('拍摄'),
            subtitle: const Text('拍照或录像', style: TextStyle(fontSize: 12, color: Colors.grey)),
            onTap: () => Navigator.pop(ctx, 'camera'),
          ),
          ListTile(
            leading: const Icon(Icons.photo_library_outlined),
            title: const Text('从相册选择'),
            subtitle:
                const Text('最多 9 张，可多选', style: TextStyle(fontSize: 12, color: Colors.grey)),
            onTap: () => Navigator.pop(ctx, 'gallery'),
          ),
        ]),
      ),
    );
    if (choice == null || !mounted) return;
    try {
      if (choice == 'camera') {
        final f = await pickCamera(context);
        if (f != null && mounted) {
          _addFile(isVideoPath(f.path) ? f : await ensureJpeg(f));
        }
      } else {
        final remain = 9 - _files.length;
        if (remain <= 0) {
          if (mounted) showToast(context, '最多 9 张');
          return;
        }
        final fs = await pickAssets(context, max: remain);
        if (!mounted) return;
        for (final f in fs) {
          _addFile(isVideoPath(f.path) ? f : await ensureJpeg(f));
        }
      }
    } catch (_) {}
  }

  void _addFile(File f) {
    if (_files.length >= 9) {
      showToast(context, '最多 9 张');
      return;
    }
    try {
      final isVideo = isMomentVideo(f.path);
      final limit = isVideo ? 50 * 1024 * 1024 : 10 * 1024 * 1024;
      if (f.lengthSync() > limit) {
        showToast(context, isVideo ? '视频超过 50MB，已跳过' : '图片超过 10MB，已跳过');
        return;
      }
      setState(() => _files.add(f));
    } catch (_) {}
  }

  Future<void> _send() async {
    if (_text.text.trim().isEmpty && _files.isEmpty) {
      showToast(context, '写点什么吧');
      return;
    }
    setState(() => _sending = true);
    try {
      final vis = _visMode == 'only' ? _visSel.toList() : <int>[];
      final hid = _visMode == 'except' ? _visSel.toList() : <int>[];
      final r = await Api.momentPost(_text.text.trim(), _files, visibleTo: vis, hiddenFrom: hid);
      if (!mounted) return;
      if (r['ok'] != true) {
        showToast(context, '${r['error'] ?? '发布失败'}');
        return;
      }
      Navigator.pop(context, true);
    } catch (_) {
      if (mounted) showToast(context, '发布失败，请检查网络');
    } finally {
      if (mounted) setState(() => _sending = false); // 失败也要复位，避免按钮永久锁死
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('发朋友圈'), actions: [
        TextButton(
          onPressed: _sending ? null : _send,
          child: Text(_sending ? '发布中…' : '发布', style: const TextStyle(color: kWeChatTheme)),
        ),
      ]),
      body: ListView(padding: const EdgeInsets.all(12), children: [
        TextField(
          controller: _text,
          maxLines: 5,
          decoration: const InputDecoration(hintText: '这一刻的想法…', border: InputBorder.none),
        ),
        const SizedBox(height: 10),
        Wrap(spacing: 8, runSpacing: 8, children: [
          ..._files.asMap().entries.map((e) => Stack(children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: isMomentVideo(e.value.path)
                      ? Container(
                          width: 90,
                          height: 90,
                          color: Colors.black87,
                          child: const Icon(Icons.play_circle_outline, color: Colors.white, size: 30),
                        )
                      : Image.file(e.value, width: 90, height: 90, fit: BoxFit.cover),
                ),
                Positioned(
                  right: 0,
                  top: 0,
                  child: GestureDetector(
                    onTap: () => setState(() => _files.removeAt(e.key)),
                    child: Container(
                      color: Colors.black54,
                      child: const Icon(Icons.close, size: 16, color: Colors.white),
                    ),
                  ),
                ),
              ])),
          GestureDetector(
            onTap: _pick,
            child: Container(
              width: 90,
              height: 90,
              color: const Color(0xFFF0F2F5),
              child: const Icon(Icons.add, color: Colors.grey, size: 30),
            ),
          ),
        ]),
        const SizedBox(height: 8),
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.visibility_outlined, size: 20, color: Colors.grey),
          title: const Text('谁可以看', style: TextStyle(fontSize: 14)),
          subtitle: Text(_visSummary(), style: const TextStyle(fontSize: 12, color: Colors.grey)),
          trailing: const Icon(Icons.chevron_right, size: 18, color: Colors.grey),
          onTap: _pickVisibility,
        ),
      ]),
    );
  }
}

// 视频预览已统一到 util 的 showVideoViewer
