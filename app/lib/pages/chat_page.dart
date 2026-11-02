import 'dart:async';
import 'dart:io';
import 'dart:math' as math;

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api.dart';
import '../emoji_data.dart';
import '../local_db.dart';
import '../log.dart';
import '../pick.dart';
import '../user_profile.dart';
import '../util.dart';
import '../wechat_icons.dart';
import 'chat_settings_page.dart';

class ChatPage extends StatefulWidget {
  final int cid;
  const ChatPage({super.key, required this.cid});

  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> with SingleTickerProviderStateMixin {
  final ScrollController _scroll = ScrollController();
  final TextEditingController _input = TextEditingController();
  final AudioPlayer _audio = AudioPlayer();
  final List<Map<String, dynamic>> _messages = [];
  final Set<int> _ids = {};

  String _name = '';
  String? _avatar;
  int _lastId = 0;
  // 数据代际：回溯/清空/全量重拉后 +1，用于丢弃在途的旧长轮询响应（防已删消息"复活"）
  int _epoch = 0;
  bool _typing = false;
  bool _busy = false;
  bool _showPlus = false;
  bool _showEmoji = false;
  int _emojiTab = 0;
  bool _emojiSearch = false;
  final TextEditingController _emojiQuery = TextEditingController();
  List<String> _emojiRecent = [];
  String? _userAvatar;
  bool _voiceMode = false;
  bool _recording = false;
  bool _recCancel = false;
  DateTime? _recStart;
  String _micMode = 'send';
  final AudioRecorder _recorder = AudioRecorder();
  String _bg = '';
  String? _playingUrl;
  DateTime? _lastPollLog;
  bool _hasText = false;
  bool _recStarting = false;
  bool _stopPending = false;
  bool _stopSend = true;
  Timer? _draftTimer;
  int _unread = 0; // 用户上滑看历史时收到的新消息数（显示"n条新消息"角标）
  bool _nearBottom = true; // 是否在列表底部（reverse 列表 offset 0 = 底部）

  OverlayEntry? _menuEntry;
  late final AnimationController _waveCtrl;

  @override
  void initState() {
    super.initState();
    _waveCtrl = AnimationController(vsync: this, duration: const Duration(milliseconds: 900));
    _scroll.addListener(_onScroll);
    _audio.onPlayerComplete.listen((_) => _setPlaying(null));
    SharedPreferences.getInstance().then((p) {
      final r = p.getStringList('emoji_recent');
      if (r != null && mounted) setState(() => _emojiRecent = List<String>.from(r));
    });
    _init();
  }

  void _setPlaying(String? url) {
    if (!mounted) return;
    setState(() => _playingUrl = url);
    if (url == null) {
      _waveCtrl.stop();
    } else if (!_waveCtrl.isAnimating) {
      _waveCtrl.repeat();
    }
  }

  @override
  void dispose() {
    _removeMenu();
    _draftTimer?.cancel();
    // 退出时把未发送的输入存为草稿（下次进入自动恢复）
    LocalDb.setDraft(widget.cid, _input.text);
    _waveCtrl.dispose();
    _recorder.dispose();
    _input.dispose();
    _emojiQuery.dispose();
    _scroll.removeListener(_onScroll);
    _scroll.dispose();
    _audio.dispose();
    super.dispose();
  }


  Future<void> _init() async {
    try {
      final p = await UserProfile.load();
      if (mounted) setState(() => _userAvatar = p.avatar.isEmpty ? null : p.avatar);
    } catch (_) {}
    try {
      final prefs = await SharedPreferences.getInstance();
      _micMode = prefs.getString('mic_mode_${widget.cid}') ?? 'send';
    } catch (_) {}
    final c = await LocalDb.getChar(widget.cid);
    if (c != null && mounted) {
      setState(() {
        _name = c['name'] ?? '';
        _avatar = c['avatar'] as String?;
      });
    }
    // 恢复未发送的草稿（仅当用户还没开始打字时）
    try {
      final d = await LocalDb.draft(widget.cid);
      if (d.isNotEmpty && mounted && _input.text.isEmpty) {
        _input.text = d;
        _input.selection = TextSelection.collapsed(offset: d.length);
        setState(() => _hasText = d.trim().isNotEmpty);
      }
    } catch (_) {}
    final local = await LocalDb.messagesFor(widget.cid);
    for (final m in local) {
      final id = (m['id'] as num).toInt();
      if (_ids.add(id)) {
        _messages.add(m);
        if (id > _lastId) _lastId = id;
      }
    }
    if (mounted) setState(() {});
    await LocalDb.setLastRead(widget.cid, _lastId == 0 ? 0 : _lastId);
    _scrollBottom(instant: true);
    Log.log('chat', 'open chat cid=${widget.cid} name=$_name local=${_messages.length} lastId=$_lastId');
    _sync();
    _pollLoop();
  }

  Future<void> _sync() async {
    try {
      // 首次打开（本地无缓存）直接全量拉取，避免先查一次增量再全量查两次
      if (_lastId == 0) {
        final full = await Api.chatMessages(widget.cid, 0, wait: false);
        if (!mounted) return;
        final fullList = (full['messages'] as List?) ?? [];
        final msgs = fullList.map((e) => (e as Map).cast<String, dynamic>()).toList();
        await LocalDb.replaceMessages(widget.cid, msgs);
        _epoch++; // 全量重拉：作废在途轮询响应
        _messages.clear();
        _ids.clear();
        _lastId = 0;
        for (final m in msgs) {
          final id = (m['id'] as num).toInt();
          if (_ids.add(id)) {
            _messages.add(m);
            if (id > _lastId) _lastId = id;
          }
        }
        _applyChat(full);
        if (!mounted) return;
        setState(() {});
        _scrollBottom();
        await LocalDb.setLastRead(widget.cid, _lastId);
        return;
      }
      final r = await Api.chatMessages(widget.cid, _lastId, wait: false);
      _applyChat(r);
      final total = (r['total'] as num?)?.toInt() ?? 0;
      final list = (r['messages'] as List?) ?? [];
      final localCount = _messages.length;
      if (total < localCount) {
        // 服务端记录比本地少（被清空/单条删除/重置）→ 全量重拉
        final full = await Api.chatMessages(widget.cid, 0, wait: false);
        final fullList = (full['messages'] as List?) ?? [];
        final msgs = fullList.map((e) => (e as Map).cast<String, dynamic>()).toList();
        await LocalDb.replaceMessages(widget.cid, msgs);
        _epoch++; // 全量重拉：作废在途轮询响应
        _messages.clear();
        _ids.clear();
        _lastId = 0;
        for (final m in msgs) {
          final id = (m['id'] as num).toInt();
          if (_ids.add(id)) {
            _messages.add(m);
            if (id > _lastId) _lastId = id;
          }
        }
        _applyChat(full);
        if (!mounted) return;
        setState(() {});
        _scrollBottom();
      } else {
        final news = _extractNew(list);
        if (news.isNotEmpty) {
          await LocalDb.insertMessages(widget.cid, news);
          if (!mounted) return;
          _afterIncoming(news);
        }
      }
      if (mounted) await LocalDb.setLastRead(widget.cid, _lastId);
    } catch (e) {
      Log.error('sync', '聊天同步失败 cid=${widget.cid}: $e');
    }
  }

  void _applyChat(Map<String, dynamic> r) {
    final chat = (r['chat'] as Map?)?.cast<String, dynamic>();
    final ch = (r['character'] as Map?)?.cast<String, dynamic>();
    if (!mounted) return;
    setState(() {
      _typing = r['typing'] == true;
      if (chat != null) _bg = (chat['background'] ?? '') as String;
      if (ch != null) {
        _name = (ch['name'] ?? _name) as String;
        _avatar = ch['avatar'] as String?;
      }
    });
  }

  List<Map<String, dynamic>> _extractNew(List raw) {
    final out = <Map<String, dynamic>>[];
    for (final e in raw) {
      final m = (e as Map).cast<String, dynamic>();
      final id = (m['id'] as num).toInt();
      if (_ids.add(id)) {
        out.add(m);
        if (id > _lastId) _lastId = id;
      }
    }
    if (out.isNotEmpty) {
      _messages.addAll(out);
    }
    return out;
  }

  Future<void> _pollLoop() async {
    while (mounted) {
      try {
        // App 不在前台时暂停轮询（避免后台重复请求/误标已读），回前台后自动恢复
        if (WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed) {
          await Future.delayed(const Duration(seconds: 2));
          continue;
        }
        final gen = _epoch;
        final r = await Api.chatMessages(widget.cid, _lastId, wait: true);
        if (!mounted) break;
        if (gen != _epoch) continue; // 期间发生过回溯/重置：丢弃这批过期响应，避免已删消息"复活"
        final news = _extractNew((r['messages'] as List?) ?? []);
        if (news.isNotEmpty) {
          await LocalDb.insertMessages(widget.cid, news);
          if (!mounted) break;
          await LocalDb.setLastRead(widget.cid, _lastId);
          if (!mounted) break;
          _afterIncoming(news);
        }
        _applyChat(r);
        // 服务器记录少于本地（被清空/单条删除）→ 全量重拉
        final total = (r['total'] as num?)?.toInt() ?? 0;
        if (total < _messages.length) {
          await _sync();
        }
      } catch (e) {
        if (!mounted) break;
        final s = '$e';
        if (s.contains('HTTP 404')) {
          // 角色被删除：停止轮询并退出（仅当聊天页在栈顶时弹出，避免误弹设置/预览页）
          showToast(context, '角色已不存在');
          if (ModalRoute.of(context)?.isCurrent ?? false) Navigator.maybePop(context);
          break;
        }
        final now = DateTime.now();
        if (_lastPollLog == null || now.difference(_lastPollLog!).inSeconds >= 30) {
          _lastPollLog = now;
          Log.error('poll', '长轮询失败 cid=${widget.cid}: $e');
        }
        await Future.delayed(const Duration(seconds: 3));
      }
    }
  }

  void _scrollBottom({bool instant = false}) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      if (instant) {
        _scroll.jumpTo(0);
      } else {
        _scroll.animateTo(0, duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      }
    });
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    final near = _scroll.offset < 80;
    if (near == _nearBottom) return;
    _nearBottom = near;
    // 用户自己滑回底部 → 清掉新消息角标
    if (near && _unread > 0 && mounted) setState(() => _unread = 0);
  }

  /// 收到新消息（微信风格）：在底部就自动滚到底；上滑看历史时不打断，
  /// 只累计角标，等用户点"n条新消息"再跳。自己发的消息（含媒体回显）总是滚到底。
  void _afterIncoming(List<Map<String, dynamic>> news) {
    final userEcho = news.any((m) => m['role'] == 'user');
    setState(() {
      if (!_nearBottom && !userEcho) _unread += news.length;
    });
    if (_nearBottom || userEcho) _scrollBottom();
  }

  void _jumpToNew() {
    if (_unread > 0 && mounted) setState(() => _unread = 0);
    _scrollBottom();
  }

  Widget _newMsgPill() {
    final visible = _unread > 0;
    return IgnorePointer(
      ignoring: !visible,
      child: AnimatedOpacity(
        opacity: visible ? 1 : 0,
        duration: const Duration(milliseconds: 160),
        child: GestureDetector(
          onTap: _jumpToNew,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(16),
              boxShadow: [
                BoxShadow(
                    color: Colors.black.withValues(alpha: 0.08),
                    blurRadius: 12,
                    offset: const Offset(0, 3)),
              ],
            ),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Container(
                width: 15,
                height: 15,
                decoration: const BoxDecoration(color: Color(0xFF07C160), shape: BoxShape.circle),
                child: const Icon(Icons.keyboard_arrow_up, size: 12, color: Colors.white),
              ),
              const SizedBox(width: 5),
              Text('$_unread条新消息',
                  style: const TextStyle(
                      fontSize: 12.5, color: Color(0xFF07C160), fontWeight: FontWeight.w500)),
            ]),
          ),
        ),
      ),
    );
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty || _busy) return;
    _busy = true;
    final snapshot = _input.text;
    setState(() {
      _showPlus = false;
      _showEmoji = false;
    });
    try {
      await Api.send(widget.cid, text);
      if (mounted && _input.text == snapshot) {
        _input.clear(); // 发送期间用户没再打字才清空
        setState(() => _hasText = false);
        LocalDb.setDraft(widget.cid, ''); // 已发出：清掉草稿
      }
      Log.log('chat', 'sent cid=${widget.cid}: ${text.substring(0, text.length > 30 ? 30 : text.length)}');
    } catch (e, st) {
      Log.error('chat', '发送失败 cid=${widget.cid}: $e', st);
      if (mounted) {
        if (_input.text.isEmpty) {
          _input.text = text; // 用户没输入新内容才恢复，避免覆盖
          _input.selection = TextSelection.collapsed(offset: _input.text.length);
        }
        showToast(context, '发送失败，请检查网络');
      }
    }
    _busy = false;
  }

  Future<void> _pickPhoto() async {
    setState(() {
      _showPlus = false;
      _showEmoji = false;
    });
    try {
      final fs = await pickImagesJpeg(context, max: 1);
      if (fs.isEmpty || !mounted) return;
      await _uploadImage(fs.first);
    } catch (e, st) {
      Log.error('chat', '图片发送失败 cid=${widget.cid}: $e', st);
      if (mounted) showToast(context, '图片发送失败');
    }
  }

  Future<void> _pickCameraAsset() async {
    setState(() {
      _showPlus = false;
      _showEmoji = false;
    });
    try {
      final f = await pickCamera(context);
      if (f == null || !mounted) return;
      if (isVideoPath(f.path)) {
        await _uploadVideo(f);
      } else {
        await _uploadImage(await ensureJpeg(f));
      }
    } catch (e, st) {
      Log.error('chat', '拍摄发送失败 cid=${widget.cid}: $e', st);
      if (mounted) showToast(context, '发送失败');
    }
  }

  Future<void> _pickVideo() async {
    setState(() {
      _showPlus = false;
      _showEmoji = false;
    });
    try {
      final fs = await pickAssets(context, max: 1, type: RequestType.video);
      if (fs.isEmpty || !mounted) return;
      await _uploadVideo(fs.first);
    } catch (e, st) {
      Log.error('chat', '视频发送失败 cid=${widget.cid}: $e', st);
      if (mounted) showToast(context, '视频发送失败（需 ≤50MB）');
    }
  }

  Future<void> _uploadImage(File f) async {
    int sz = 0;
    try {
      sz = f.lengthSync();
    } catch (_) {}
    if (sz > 10 * 1024 * 1024) {
      if (mounted) showToast(context, '图片超过 10MB');
      return;
    }
    await Api.sendImage(widget.cid, f);
    Log.log('chat', 'sent image cid=${widget.cid}, $sz bytes');
  }

  Future<void> _uploadVideo(File f) async {
    int sz = 0;
    try {
      sz = f.lengthSync();
    } catch (_) {}
    if (sz > 50 * 1024 * 1024) {
      if (mounted) showToast(context, '视频超过 50MB');
      return;
    }
    if (mounted) showToast(context, '视频上传中…');
    await Api.sendVideo(widget.cid, f);
    Log.log('chat', 'sent video cid=${widget.cid}, $sz bytes');
  }

  // 消息回溯：长按消息 → 删除其后所有消息（含摘要/作弊设定的同步处理）
  Future<void> _showRewindDialog(Map<String, dynamic> m) async {
    final id = (m['id'] as num?)?.toInt() ?? 0;
    if (id <= 0) return;
    final raw = cleanMetaTime((m['content'] ?? '') as String);
    final preview = raw.length > 40 ? '${raw.substring(0, 40)}…' : raw;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('回溯'),
        content: Text(preview.isEmpty ? '回到这条消息？其后的消息会被删除。' : '回到这条消息？其后的消息会被删除。\n\n「$preview」'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消', style: TextStyle(color: Colors.black)),
          ),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('回溯')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      final r = await Api.rewind(widget.cid, id);
      if (r['ok'] == true) {
        if (mounted) showToast(context, '已回溯（删除 ${r['deleted'] ?? 0} 条）');
        _epoch++; // 回溯：作废在途的旧轮询响应
        _lastId = 0;
        _messages.clear();
        _ids.clear();
        await _sync();
      } else {
        if (mounted) showToast(context, '${r['error'] ?? '回溯失败'}');
      }
    } catch (_) {
      if (mounted) showToast(context, '回溯失败（网络错误）');
    }
  }

  void _removeMenu() {
    _menuEntry?.remove();
    _menuEntry = null;
  }

  Future<void> _copyMessage(Map<String, dynamic> m) async {
    final text = cleanMetaTime('${m['content'] ?? ''}');
    if (text.isEmpty) return;
    await Clipboard.setData(ClipboardData(text: text));
    if (mounted) showToast(context, '已复制');
  }

  /// 微信式深色弹窗菜单（长按气泡弹出，锚定在长按位置上方）
  void _showBubbleMenu(Map<String, dynamic> m, Offset pos) {
    _removeMenu();
    final w = MediaQuery.of(context).size.width;
    final h = MediaQuery.of(context).size.height;
    final entry = OverlayEntry(builder: (_) {
      return Stack(children: [
        Positioned.fill(
          child: GestureDetector(
            behavior: HitTestBehavior.translucent,
            onTap: _removeMenu,
            onLongPress: _removeMenu,
          ),
        ),
        Positioned(
          left: (pos.dx - 74).clamp(8.0, w - 148.0),
          top: (pos.dy - 96).clamp(60.0, h - 150.0),
          child: Material(
            color: Colors.transparent,
            child: Container(
              decoration: BoxDecoration(
                color: const Color(0xF2262626),
                borderRadius: BorderRadius.circular(8),
              ),
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 8),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                _menuBtn(Icons.copy_outlined, '复制', () {
                  _removeMenu();
                  _copyMessage(m);
                }),
                const SizedBox(width: 4),
                _menuBtn(Icons.settings_backup_restore, '回溯', () {
                  _removeMenu();
                  _showRewindDialog(m);
                }),
              ]),
            ),
          ),
        ),
      ]);
    });
    _menuEntry = entry;
    Overlay.of(context).insert(entry);
  }

  Widget _menuBtn(IconData icon, String label, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 56,
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(icon, size: 20, color: Colors.white),
          const SizedBox(height: 4),
          Text(label, style: const TextStyle(fontSize: 11, color: Colors.white)),
        ]),
      ),
    );
  }

  Future<void> _playVoice(Map<String, dynamic> m) async {
    final url = m['media_url'];
    if (url == null || url == '') return;
    final full = fullUrl('$url');
    try {
      if (_playingUrl == url) {
        await _audio.stop();
        _setPlaying(null);
        return;
      }
      await _audio.stop();
      await _audio.play(UrlSource(full));
      _setPlaying(url as String);
    } catch (_) {}
  }

  Future<void> _playVideo(Map<String, dynamic> m) async {
    final url = m['media_url'];
    if (url == null || url == '') return;
    final full = fullUrl('$url');
    Log.log('video', 'play $full');
    showVideoViewer(context, full);
  }

  Future<void> _openSettings() async {
    await Navigator.push(
        context,
        MaterialPageRoute(
            builder: (_) => ChatSettingsPage(cid: widget.cid, name: _name, avatar: _avatar)));
    _sync();
  }

  @override
  Widget build(BuildContext context) {
    final bgImage = _bg.isNotEmpty ? fullUrl(_bg) : null;
    final entries = _entries; // 一次 build 只计算一次，避免 itemBuilder 内 O(n) 重算
    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          icon: const WcBackIcon(size: 24),
          onPressed: () => Navigator.of(context).maybePop(),
        ),
        title: Text(_name.isEmpty ? '聊天' : _name),
        actions: [
          IconButton(icon: const WcMoreIcon(size: 24), tooltip: '聊天信息', onPressed: _openSettings),
          const SizedBox(width: 4),
        ],
      ),
      body: Container(
        decoration: bgImage != null
            ? BoxDecoration(
                image: DecorationImage(image: NetworkImage(bgImage), fit: BoxFit.cover),
              )
            : null,
        child: Column(
          children: [
            Expanded(
              child: Stack(
                children: [
                  Positioned.fill(
                    child: ListView.builder(
                      controller: _scroll,
                      reverse: true,
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                      itemCount: entries.length + (_typing ? 1 : 0),
                      itemBuilder: (ctx, i) {
                        if (_typing && i == 0) return _typingBubble();
                        final idx = i - (_typing ? 1 : 0);
                        final e = entries[entries.length - 1 - idx];
                        if (e is double) return _timeSeparator(e);
                        return _bubble(e as Map<String, dynamic>);
                      },
                    ),
                  ),
                  Positioned(top: 8, right: 12, child: _newMsgPill()),
                ],
              ),
            ),
            _inputBar(),
          ],
        ),
      ),
    );
  }

  /// 微信风格渲染序列：消息间隔超过 5 分钟时，在中间插入时间分隔条
  List<Object> get _entries {
    final out = <Object>[];
    double prev = 0;
    for (final m in _messages) {
      final ts = ((m['ts'] ?? 0) as num).toDouble();
      if (ts > 0 && (out.isEmpty || ts - prev > 300)) out.add(ts);
      out.add(m);
      prev = ts > 0 ? ts : prev;
    }
    return out;
  }

  Widget _timeSeparator(double ts) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 10),
        child: Text(wechatChatTime(ts),
            style: const TextStyle(fontSize: 12.5, color: Color(0xFF999999))),
      ),
    );
  }

  Widget _typingBubble() {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.92),
          borderRadius: BorderRadius.circular(6),
        ),
        child: const Text('正在输入…', style: TextStyle(fontSize: 13, color: Color(0xFF888888))),
      ),
    );
  }

  Widget _bubble(Map<String, dynamic> m) {
    final isUser = m['role'] == 'user';
    final kind = m['kind'] ?? 'text';
    final avatarUrl = isUser ? _userAvatar : _avatarUrl(_avatar);
    final avatar = DefaultAvatar(url: avatarUrl, size: 40);

    Widget body;
    if (kind == 'sticker') {
      body = ClipRRect(
        borderRadius: BorderRadius.circular(6),
        child: Image.network(fullUrl('${m['media_url']}'), width: 110, cacheWidth: 240, errorBuilder: (_, _, _) {
          return const Text('[表情]', style: TextStyle(color: Colors.grey));
        }),
      );
    } else if (kind == 'image' || kind == 'photo') {
      body = GestureDetector(
        onTap: () => showImageViewer(context, fullUrl('${m['media_url']}')),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: Image.network(
            fullUrl('${m['media_url']}'),
            width: 180,
            fit: BoxFit.cover,
            cacheWidth: 400,
            loadingBuilder: (c, w, p) {
              if (p == null) return w; // 加载完成 → 显示图片本身
              return Container(
                width: 180,
                height: 140,
                color: Colors.black12,
                child: const Center(
                    child: SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))),
              );
            },
            errorBuilder: (_, _, _) => Container(
              width: 180,
              height: 100,
              color: Colors.black12,
              child: const Center(child: Text('[图片加载失败]', style: TextStyle(fontSize: 12, color: Colors.grey))),
            ),
          ),
        ),
      );
      final caption = cleanMetaTime((m['content'] ?? '') as String);
      if (caption.isNotEmpty && kind == 'image') {
        body = Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [body, const SizedBox(height: 5), Text(caption, style: const TextStyle(fontSize: 15))],
        );
      }
    } else if (kind == 'video') {
      final dur = ((m['duration'] ?? 0) as num).toDouble();
      body = GestureDetector(
        onTap: () => _playVideo(m),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: SizedBox(
            width: 200,
            height: 132,
            child: Stack(alignment: Alignment.center, children: [
              VideoThumb(url: fullUrl('${m['media_url']}'), width: 200, height: 132),
              Container(
                width: 46,
                height: 46,
                decoration: const BoxDecoration(color: Colors.black45, shape: BoxShape.circle),
                child: const Icon(Icons.play_arrow, color: Colors.white, size: 32),
              ),
              if (dur > 0)
                Positioned(
                  right: 6,
                  bottom: 6,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(
                        color: Colors.black54, borderRadius: BorderRadius.circular(4)),
                    child: Text("${dur.round()}''",
                        style: const TextStyle(color: Colors.white, fontSize: 11)),
                  ),
                ),
            ]),
          ),
        ),
      );
    } else if (kind == 'voice') {
      final raw = ((m['duration'] ?? 0) as num).toDouble();
      final dur = raw.clamp(1.0, 60.0);
      final playing = _playingUrl == m['media_url'];
      final label = Text("${math.max(1, raw.round())}''",
          style: const TextStyle(fontSize: 15, color: Color(0xFF1A1A1A)));
      Widget wave = _VoiceWaves(left: !isUser, active: playing, t: 0);
      if (playing) {
        wave = AnimatedBuilder(
          animation: _waveCtrl,
          builder: (_, _) => _VoiceWaves(left: !isUser, active: true, t: _waveCtrl.value),
        );
      }
      body = SizedBox(
        width: 36.0 + (dur / 60.0) * 130.0,
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: isUser ? [label, wave] : [wave, label],
        ),
      );
    } else {
      body = Text(
        cleanMetaTime((m['content'] ?? '') as String), // 兜底剥离历史缓存里的时间标注
        style: const TextStyle(fontSize: 16, height: 1.35, color: Colors.black),
      );
    }

    Widget bubble = Container(
      padding: kind == 'sticker' ? EdgeInsets.zero : const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: kind == 'sticker'
          ? null
          : BoxDecoration(
              color: isUser ? kWeChatGreen : Colors.white,
              borderRadius: BorderRadius.circular(6),
            ),
      child: body,
    );

    if (kind == 'voice') {
      bubble = GestureDetector(onTap: () => _playVoice(m), child: bubble);
    }

    // 长按气泡 → 微信式深色弹窗菜单（复制 / 回溯）
    bubble = GestureDetector(
      onLongPressStart: (d) => _showBubbleMenu(m, d.globalPosition),
      child: bubble,
    );

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
        children: [
          if (!isUser) avatar,
          if (!isUser) const SizedBox(width: 8),
          if (!isUser && kind != 'sticker') const BubbleTail(left: true, color: Colors.white),
          Flexible(
            child: ConstrainedBox(
              constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.68),
              child: bubble,
            ),
          ),
          if (isUser && kind != 'sticker') const BubbleTail(left: false, color: kWeChatGreen),
          if (isUser) const SizedBox(width: 8),
          if (isUser) avatar,
        ],
      ),
    );
  }

  void _toggleEmoji() {
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() {
      _showEmoji = !_showEmoji;
      if (_showEmoji) {
        _showPlus = false;
        _emojiSearch = false;
      }
    });
  }

  void _togglePlus() {
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() {
      _showPlus = !_showPlus;
      if (_showPlus) _showEmoji = false;
    });
  }

  Widget _emojiPanel() {
    return Container(
      height: 290,
      color: const Color(0xFFF7F7F7),
      child: Column(children: [
        Container(
          height: 46,
          color: Colors.white,
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Row(children: [
              _emojiTabBtn(
                icon: Icons.search,
                selected: _emojiSearch,
                onTap: () => setState(() {
                  _emojiSearch = true;
                  _emojiQuery.clear();
                }),
              ),
              for (var i = 0; i < kEmojiCats.length; i++)
                _emojiTabBtn(
                  text: kEmojiCats[i].icon,
                  selected: !_emojiSearch && _emojiTab == i,
                  onTap: () => setState(() {
                    _emojiSearch = false;
                    _emojiTab = i;
                  }),
                ),
            ]),
          ),
        ),
        Expanded(
          child: Stack(children: [
            Positioned.fill(child: _emojiSearch ? _emojiSearchView() : _emojiGridView()),
            Positioned(
              right: 10,
              bottom: 10,
              child: GestureDetector(
                onTap: _emojiBackspace,
                onLongPress: () => setState(() => _input.clear()),
                child: Container(
                  width: 56,
                  height: 38,
                  decoration:
                      BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8)),
                  child: const Icon(Icons.backspace_outlined, size: 20, color: Color(0xFF666666)),
                ),
              ),
            ),
          ]),
        ),
      ]),
    );
  }

  Widget _emojiTabBtn({IconData? icon, String? text, required bool selected, required VoidCallback onTap}) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 40,
        height: 46,
        alignment: Alignment.center,
        child: Container(
          width: 36,
          height: 36,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected ? Colors.white : Colors.transparent,
            borderRadius: BorderRadius.circular(8),
            boxShadow: selected
                ? const [BoxShadow(color: Color(0x14000000), blurRadius: 4, offset: Offset(0, 1))]
                : null,
          ),
          child: icon != null
              ? Icon(icon, size: 22, color: const Color(0xFF333333))
              : Text(text ?? '', style: const TextStyle(fontSize: 21)),
        ),
      ),
    );
  }

  Widget _emojiSection(String title, List<String> items) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
        child: Text(title, style: const TextStyle(fontSize: 13, color: Color(0xFF888888))),
      ),
      GridView.count(
        crossAxisCount: 8,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        childAspectRatio: 1.15,
        padding: const EdgeInsets.symmetric(horizontal: 10),
        children: [
          for (final it in items)
            GestureDetector(
              onTap: () => _insertEmoji(emojiOf(it)),
              child: Center(child: Text(emojiOf(it), style: const TextStyle(fontSize: 26))),
            ),
        ],
      ),
    ]);
  }

  Widget _emojiGridView() {
    final items = kEmojiCats[_emojiTab].items;
    return ListView(
      padding: const EdgeInsets.only(bottom: 56),
      children: [
        if (_emojiRecent.isNotEmpty) _emojiSection('最近使用', _emojiRecent),
        _emojiSection('所有表情', items),
      ],
    );
  }

  Widget _emojiSearchView() {
    final q = _emojiQuery.text.trim();
    final results = <String>[];
    if (q.isNotEmpty) {
      for (final cat in kEmojiCats) {
        for (final it in cat.items) {
          if (it.contains(q)) results.add(it);
        }
      }
    }
    return Column(children: [
      Container(
        color: Colors.white,
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 10),
        child: Container(
          height: 34,
          padding: const EdgeInsets.symmetric(horizontal: 10),
          decoration:
              BoxDecoration(color: const Color(0xFFF2F2F2), borderRadius: BorderRadius.circular(6)),
          child: Row(children: [
            const Icon(Icons.search, size: 18, color: Color(0xFF999999)),
            const SizedBox(width: 6),
            Expanded(
              child: TextField(
                controller: _emojiQuery,
                autofocus: true,
                style: const TextStyle(fontSize: 15),
                decoration: const InputDecoration(
                  isDense: true,
                  border: InputBorder.none,
                  contentPadding: EdgeInsets.zero,
                  hintText: '搜索表情',
                ),
                onChanged: (_) => setState(() {}),
              ),
            ),
          ]),
        ),
      ),
      Expanded(
        child: q.isEmpty
            ? const Center(
                child: Text('输入关键词搜索表情',
                    style: TextStyle(fontSize: 13, color: Color(0xFFAAAAAA))))
            : GridView.count(
                crossAxisCount: 8,
                childAspectRatio: 1.15,
                padding: const EdgeInsets.fromLTRB(10, 10, 10, 56),
                children: [
                  for (final it in results)
                    GestureDetector(
                      onTap: () => _insertEmoji(emojiOf(it)),
                      child: Center(child: Text(emojiOf(it), style: const TextStyle(fontSize: 26))),
                    ),
                ],
              ),
      ),
    ]);
  }

  void _scheduleDraftSave() {
    _draftTimer?.cancel();
    _draftTimer = Timer(const Duration(milliseconds: 600), () {
      LocalDb.setDraft(widget.cid, _input.text);
    });
  }

  void _insertEmoji(String e) {
    final t = _input.text + e;
    _input.value = TextEditingValue(text: t, selection: TextSelection.collapsed(offset: t.length));
    _addRecentEmoji(e);
    _scheduleDraftSave();
    setState(() => _hasText = t.trim().isNotEmpty);
  }

  void _emojiBackspace() {
    if (_emojiSearch) {
      final t = _emojiQuery.text;
      if (t.isEmpty) return;
      final nt = t.characters.take(t.characters.length - 1).toString();
      _emojiQuery.value = TextEditingValue(text: nt, selection: TextSelection.collapsed(offset: nt.length));
      setState(() {});
      return;
    }
    final t = _input.text;
    if (t.isEmpty) return;
    final nt = t.characters.take(t.characters.length - 1).toString();
    _input.value = TextEditingValue(text: nt, selection: TextSelection.collapsed(offset: nt.length));
    _scheduleDraftSave();
    setState(() => _hasText = nt.trim().isNotEmpty);
  }

  void _addRecentEmoji(String e) {
    _emojiRecent.remove(e);
    _emojiRecent.insert(0, e);
    if (_emojiRecent.length > 8) _emojiRecent = _emojiRecent.sublist(0, 8);
    SharedPreferences.getInstance().then((p) => p.setStringList('emoji_recent', _emojiRecent));
  }

  Widget _plusPanel() {
    return Container(
      color: const Color(0xFFF7F7F7),
      padding: const EdgeInsets.fromLTRB(18, 20, 18, 16),
      child: Wrap(
        spacing: 26,
        runSpacing: 18,
        children: [
          _plusItem(Icons.photo_library_outlined, '照片', _pickPhoto),
          _plusItem(Icons.photo_camera_outlined, '拍摄', _pickCameraAsset),
          _plusItem(Icons.videocam_outlined, '视频', _pickVideo),
        ],
      ),
    );
  }

  Future<void> _startRecord() async {
    if (_recording || _recStarting) return;
    _recStarting = true;
    _stopPending = false;
    try {
      if (!await _recorder.hasPermission()) {
        _recStarting = false;
        if (mounted) showToast(context, '没有麦克风权限，请在系统设置中开启');
        return;
      }
      final dir = await getTemporaryDirectory();
      final path = '${dir.path}/rec_${DateTime.now().millisecondsSinceEpoch}.m4a';
      await _recorder.start(
        const RecordConfig(encoder: AudioEncoder.aacLc, bitRate: 96000, sampleRate: 44100),
        path: path,
      );
      _recStart = DateTime.now();
      if (_stopPending) {
        // 启动期间用户已松手：立即走停止流程，避免卡在“松开发送”
        _recording = true;
        _recStarting = false;
        await _stopRecord(send: _stopSend);
        return;
      }
      _recStarting = false;
      if (mounted) {
        setState(() {
          _recording = true;
          _recCancel = false;
        });
      }
    } catch (e) {
      _recStarting = false;
      Log.error('voice', '录音失败: $e');
      if (mounted) showToast(context, '录音失败');
    }
  }

  Future<void> _stopRecord({required bool send}) async {
    if (_recStarting) {
      // 录音仍在启动中：先记下松手动作，启动完成后立即停止
      _stopPending = true;
      _stopSend = send;
      return;
    }
    if (!_recording) return;
    final start = _recStart;
    _recStart = null;
    String? path;
    try {
      path = await _recorder.stop();
    } catch (_) {}
    if (mounted) {
      setState(() {
        _recording = false;
        _recCancel = false;
      });
    }
    if (!send) {
      if (path != null) {
        try {
          await File(path).delete();
        } catch (_) {}
      }
      return;
    }
    final dur = start == null ? 0.0 : DateTime.now().difference(start).inMilliseconds / 1000.0;
    if (path == null || dur < 1.0) {
      if (path != null) {
        try {
          await File(path).delete();
        } catch (_) {}
      }
      if (mounted) showToast(context, '说话时间太短');
      return;
    }
    await _sendVoice(File(path), dur);
  }

  Future<void> _sendVoice(File f, double dur) async {
    try {
      if (mounted) showToast(context, '语音识别中…');
      final r = await Api.voiceMessage(widget.cid, f, mode: _micMode, duration: dur);
      if (r['ok'] != true) {
        if (mounted) showToast(context, '${r['error'] ?? '语音识别失败'}');
        return;
      }
      final text = '${r['text'] ?? ''}';
      if (_micMode == 'input') {
        final t = _input.text + text;
        _input.value = TextEditingValue(text: t, selection: TextSelection.collapsed(offset: t.length));
        _scheduleDraftSave();
        if (mounted) setState(() => _hasText = t.trim().isNotEmpty);
      }
    } catch (e) {
      Log.error('voice', '语音上传失败: $e');
      if (mounted) showToast(context, '语音发送失败');
    } finally {
      try {
        await f.delete();
      } catch (_) {}
    }
  }

  Widget _inputBar() {
    final hasText = _hasText;
    return Container(
      color: const Color(0xFFF7F7F7),
      child: SafeArea(
        top: false,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (_showPlus) _plusPanel(),
            if (_showEmoji) _emojiPanel(),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 7, 10, 7),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  GestureDetector(
                    onTap: () => setState(() {
                      _voiceMode = !_voiceMode;
                      if (_voiceMode) {
                        _showPlus = false;
                        _showEmoji = false;
                      }
                    }),
                    child: Padding(
                      padding: const EdgeInsets.only(right: 8, bottom: 8),
                      child: _voiceMode
                          ? const WcKeyboardIcon(size: 26, color: Color(0xFF2C2C2C))
                          : const WcVoiceCircleIcon(size: 26, color: Color(0xFF2C2C2C)),
                    ),
                  ),
                  Expanded(
                    child: _voiceMode
                        ? Listener(
                            onPointerDown: (_) => _startRecord(),
                            onPointerMove: (e) {
                              final cancel = e.localPosition.dy < -60;
                              if (cancel != _recCancel) setState(() => _recCancel = cancel);
                            },
                            onPointerUp: (_) => _stopRecord(send: true),
                            onPointerCancel: (_) => _stopRecord(send: false),
                            child: Container(
                              height: 40,
                              alignment: Alignment.center,
                              decoration: BoxDecoration(
                                  color: _recording ? const Color(0xFFDEDEDE) : Colors.white,
                                  borderRadius: BorderRadius.circular(6)),
                              child: Text(
                                _recording ? (_recCancel ? '松开手指，取消发送' : '松开 发送') : '按住 说话',
                                style: TextStyle(
                                    fontSize: 15,
                                    color: _recCancel ? const Color(0xFFFA5151) : Colors.black),
                              ),
                            ),
                          )
                        : Container(
                            constraints: const BoxConstraints(minHeight: 40),
                            decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(6)),
                            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
                            child: Row(children: [
                              Expanded(
                                child: TextField(
                                  controller: _input,
                                  maxLines: 4,
                                  minLines: 1,
                                  textInputAction: TextInputAction.newline,
                                  style: const TextStyle(fontSize: 16),
                                  decoration: const InputDecoration(
                                    isDense: true,
                                    border: InputBorder.none,
                                    contentPadding: EdgeInsets.zero,
                                  ),
                                  onChanged: (_) {
                                    // 打字时只在“有无内容”变化时重建，减少整页 setState
                                    final h = _input.text.trim().isNotEmpty;
                                    if (h != _hasText) setState(() => _hasText = h);
                                    _scheduleDraftSave();
                                  },
                                  onTap: () {
                                    if (_showPlus || _showEmoji) {
                                      setState(() {
                                        _showPlus = false;
                                        _showEmoji = false;
                                      });
                                    }
                                  },
                                ),
                              ),
                              if (!hasText)
                                const Padding(
                                  padding: EdgeInsets.only(left: 6, bottom: 1),
                                  child: WcMicSmallIcon(size: 20, color: Color(0xFFB2B2B2)),
                                ),
                            ]),
                          ),
                  ),
                  const SizedBox(width: 10),
                  GestureDetector(
                    onTap: _toggleEmoji,
                    child: Padding(
                      padding: const EdgeInsets.only(right: 8, bottom: 6),
                      child: _showEmoji
                          ? const WcKeyboardIcon(size: 26, color: Color(0xFF2C2C2C))
                          : const WcSmileyIcon(size: 26, color: Color(0xFF2C2C2C)),
                    ),
                  ),
                  if (hasText)
                    GestureDetector(
                      onTap: _send,
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
                        decoration:
                            BoxDecoration(color: kWeChatTheme, borderRadius: BorderRadius.circular(6)),
                        child: const Text('发送', style: TextStyle(color: Colors.white, fontSize: 15)),
                      ),
                    )
                  else
                    GestureDetector(
                      onTap: _togglePlus,
                      child: const Padding(
                        padding: EdgeInsets.all(6),
                        child: WcPlusCircleIcon(size: 30, color: Color(0xFF2C2C2C)),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _plusItem(IconData icon, String label, VoidCallback onTap) {
    return InkWell(
      onTap: onTap,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 60,
            height: 60,
            decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(14)),
            child: Icon(icon, size: 28, color: const Color(0xFF555555)),
          ),
          const SizedBox(height: 6),
          Text(label, style: const TextStyle(fontSize: 12, color: Color(0xFF666666))),
        ],
      ),
    );
  }
}

String? _avatarUrl(String? a) => avatarFull(a);



class _VoiceWaves extends StatelessWidget {
  final bool left;
  final bool active;
  final double t;
  const _VoiceWaves({required this.left, required this.active, this.t = 0});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 17,
      height: 18,
      child: CustomPaint(painter: _VoiceWavesPainter(left: left, active: active, t: t)),
    );
  }
}

class _VoiceWavesPainter extends CustomPainter {
  final bool left;
  final bool active;
  final double t;
  _VoiceWavesPainter({required this.left, required this.active, required this.t});

  @override
  void paint(Canvas canvas, Size size) {
    const ink = Color(0xFF1A1A1A);
    final cy = size.height / 2;
    final cx = left ? 2.0 : size.width - 2.0;
    canvas.drawCircle(Offset(cx, cy), 1.7, Paint()..color = ink);
    final stroke = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.4
      ..strokeCap = StrokeCap.round;
    final phase = (t * 3).floor() % 3;
    for (var i = 0; i < 3; i++) {
      final lit = !active || i <= phase;
      stroke.color = lit ? ink : const Color(0x401A1A1A);
      final r = 3.4 + i * 3.6;
      final start = left ? -0.85 : math.pi - 0.85;
      canvas.drawArc(Rect.fromCircle(center: Offset(cx, cy), radius: r), start, 1.7, false, stroke);
    }
  }

  @override
  bool shouldRepaint(covariant _VoiceWavesPainter old) =>
      old.left != left || old.active != active || old.t != t;
}
