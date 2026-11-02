import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api.dart';
import '../emoji_panel.dart';
import '../local_db.dart';
import '../log.dart';
import '../pick.dart';
import '../user_profile.dart';
import '../util.dart';
import '../wechat_icons.dart';
import 'group_settings_page.dart';

/// 群聊页（对齐私聊：时间分隔、气泡尾巴、表情/加号面板、图片视频、长按复制）
class GroupChatPage extends StatefulWidget {
  final int gid;
  final String name;
  const GroupChatPage({super.key, required this.gid, this.name = ''});

  @override
  State<GroupChatPage> createState() => _GroupChatPageState();
}

class _GroupChatPageState extends State<GroupChatPage> {
  final ScrollController _scroll = ScrollController();
  final TextEditingController _input = TextEditingController();
  final List<Map<String, dynamic>> _messages = [];
  List<Map<String, dynamic>> _members = [];
  String _name = '';
  String? _userAvatar;
  int _lastId = 0;
  bool _busy = false;
  bool _showEmoji = false;
  bool _showPlus = false;
  Timer? _timer;
  OverlayEntry? _menuEntry;
  bool _syncing = false;
  bool _syncFailed = false;
  bool _hasText = false;
  Timer? _draftTimer;
  int _unread = 0; // 用户上滑看历史时收到的新消息数（显示"n条新消息"角标）
  bool _nearBottom = true; // 是否在列表底部（reverse 列表 offset 0 = 底部）

  @override
  void initState() {
    super.initState();
    _name = widget.name;
    _scroll.addListener(_onScroll);
    UserProfile.load().then((p) {
      if (mounted) setState(() => _userAvatar = p.avatar.isEmpty ? null : p.avatar);
    });
    _load();
    _restoreDraft();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _sync());
  }

  @override
  void dispose() {
    _removeMenu();
    _timer?.cancel();
    _draftTimer?.cancel();
    // 退出时把未发送的输入存为草稿
    SharedPreferences.getInstance()
        .then((p) => p.setString('gdraft_${widget.gid}', _input.text));
    _input.dispose();
    _scroll.removeListener(_onScroll);
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _restoreDraft() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final d = prefs.getString('gdraft_${widget.gid}') ?? '';
      if (d.isNotEmpty && mounted && _input.text.isEmpty) {
        _input.text = d;
        _input.selection = TextSelection.collapsed(offset: d.length);
        setState(() => _hasText = d.trim().isNotEmpty);
      }
    } catch (_) {}
  }

  void _scheduleDraftSave() {
    _draftTimer?.cancel();
    _draftTimer = Timer(const Duration(milliseconds: 600), () {
      SharedPreferences.getInstance()
          .then((p) => p.setString('gdraft_${widget.gid}', _input.text));
    });
  }

  void _clearDraft() {
    _draftTimer?.cancel();
    SharedPreferences.getInstance().then((p) => p.setString('gdraft_${widget.gid}', ''));
  }

  Future<void> _load() async {
    // 先展示本地缓存：离线/弱网也能看到历史（服务器返回后覆盖）
    if (_messages.isEmpty) {
      try {
        final cached = await LocalDb.groupMessagesFor(widget.gid);
        if (cached.isNotEmpty && mounted && _messages.isEmpty) {
          setState(() {
            _messages.addAll(cached);
            _lastId = (_messages.last['id'] as num).toInt();
          });
          _scrollBottom();
        }
      } catch (_) {}
    }
    try {
      final d = await Api.groupMessages(widget.gid, 0);
      final list =
          ((d['messages'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
      if (!mounted) return;
      setState(() {
        _messages
          ..clear()
          ..addAll(list);
        _lastId = _messages.isEmpty ? 0 : (_messages.last['id'] as num).toInt();
        _members =
            ((d['members'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
        final n = '${d['name'] ?? ''}';
        if (n.isNotEmpty) _name = n;
        _syncFailed = false;
      });
      _scrollBottom();
      unawaited(LocalDb.replaceGroupMessages(widget.gid, list));
    } catch (e) {
      if ('$e'.contains('HTTP 404')) {
        _handleGone();
        return;
      }
      Log.error('group', '加载群聊失败 gid=${widget.gid}: $e');
    }
  }

  void _handleGone() {
    _timer?.cancel();
    unawaited(LocalDb.deleteGroup(widget.gid));
    if (!mounted) return;
    showToast(context, '群聊已被删除');
    if (ModalRoute.of(context)?.isCurrent ?? false) Navigator.maybePop(context);
  }

  Future<void> _sync() async {
    if (_syncing) return; // 防重入：接口慢时不叠加请求
    if (WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed) return; // 后台不轮询
    _syncing = true;
    try {
      final d = await Api.groupMessages(widget.gid, _lastId);
      final total = (d['total'] as num?)?.toInt() ?? 0;
      if (total > 0 && total < _messages.length) {
        // 服务端条数少于本地（后台清空/批量删除）→ 全量重拉，避免旧气泡常驻
        await _load();
        return;
      }
      final list =
          ((d['messages'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
      if (!mounted) return;
      final wasFailed = _syncFailed;
      if (list.isNotEmpty) {
        final ids = _messages.map((m) => m['id']).toSet();
        var added = false;
        final newOnes = <Map<String, dynamic>>[];
        for (final m in list) {
          if (ids.add(m['id'])) {
            _messages.add(m);
            newOnes.add(m);
            added = true;
          }
        }
        if (newOnes.isNotEmpty) unawaited(LocalDb.insertGroupMessages(widget.gid, newOnes));
        if (added) _lastId = (_messages.last['id'] as num).toInt();
        if (added || wasFailed) {
          final userEcho = newOnes.any((m) => m['role'] == 'user');
          setState(() {
            _syncFailed = false;
            if (added && !_nearBottom && !userEcho) _unread += newOnes.length;
          });
          if (_nearBottom || userEcho) _scrollBottom();
        }
      } else if (wasFailed) {
        setState(() => _syncFailed = false);
      }
    } catch (e) {
      if ('$e'.contains('HTTP 404')) {
        _handleGone();
        return;
      }
      if (!_syncFailed) {
        _syncFailed = true;
        if (mounted) setState(() {});
      }
      Log.error('group', '同步失败 gid=${widget.gid}: $e');
    } finally {
      _syncing = false;
    }
  }

  void _scrollBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) _scroll.jumpTo(0);
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

  void _jumpToNew() {
    if (_unread > 0 && mounted) setState(() => _unread = 0);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(0, duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      }
    });
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
    try {
      await Api.groupSend(widget.gid, text);
      // 只清掉"本次真正发出去的那条"：发送期间用户又打了新内容就保留
      // （原实现无条件 clear，会把新输入一起吃掉）
      final keepNew = _input.text.trim() != text;
      _clearDraft(); // 已发出：清掉草稿
      if (!keepNew) {
        _input.clear();
        if (mounted) {
          setState(() {
            _hasText = false;
            _showPlus = false;
            _showEmoji = false;
          });
        }
      }
      await _sync();
    } catch (e) {
      if (mounted) showToast(context, '发送失败');
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
      showToast(context, '上传中…');
      await Api.groupSendImage(widget.gid, fs.first);
      await _sync();
    } catch (e) {
      Log.error('group', '发图失败: $e');
      if (mounted) showToast(context, '发送失败');
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
      showToast(context, '上传中…');
      if (isVideoPath(f.path)) {
        await Api.groupSendVideo(widget.gid, f);
      } else {
        await Api.groupSendImage(widget.gid, await ensureJpeg(f));
      }
      await _sync();
    } catch (e) {
      Log.error('group', '拍摄发送失败: $e');
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
      showToast(context, '上传中…');
      await Api.groupSendVideo(widget.gid, fs.first);
      await _sync();
    } catch (e) {
      Log.error('group', '发视频失败: $e');
      if (mounted) showToast(context, '发送失败');
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

  void _showMenu(Map<String, dynamic> m, Offset pos) {
    final text = cleanMetaTime('${m['content'] ?? ''}');
    if (text.isEmpty) return;
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
          left: (pos.dx - 40).clamp(8.0, w - 96.0),
          top: (pos.dy - 96).clamp(60.0, h - 150.0),
          child: Material(
            color: Colors.transparent,
            child: Container(
              decoration: BoxDecoration(
                  color: const Color(0xF2262626), borderRadius: BorderRadius.circular(8)),
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 8),
              child: GestureDetector(
                onTap: () {
                  _removeMenu();
                  _copyMessage(m);
                },
                child: Container(
                  width: 56,
                  padding: const EdgeInsets.symmetric(vertical: 6),
                  child: Column(mainAxisSize: MainAxisSize.min, children: const [
                    Icon(Icons.copy_outlined, size: 20, color: Colors.white),
                    SizedBox(height: 4),
                    Text('复制', style: TextStyle(fontSize: 11, color: Colors.white)),
                  ]),
                ),
              ),
            ),
          ),
        ),
      ]);
    });
    _menuEntry = entry;
    Overlay.of(context).insert(entry);
  }

  String? _avatar(String? a) {
    if (a == null || a.isEmpty) return null;
    if (a.startsWith('http') || a.startsWith('file://')) return a;
    return fullUrl(a);
  }

  String? _memberAvatar(Map<String, dynamic> m) {
    final cid = m['cid'];
    for (final x in _members) {
      if (x['id'] == cid) return _avatar(x['avatar'] as String?);
    }
    return null;
  }

  Future<void> _openSettings() async {
    final r = await Navigator.push(
        context, MaterialPageRoute(builder: (_) => GroupSettingsPage(gid: widget.gid)));
    if (!mounted) return;
    if (r == true) {
      Navigator.pop(context);
      return;
    }
    await _load();
  }

  /// 时间分隔（与私聊一致：间隔超过 5 分钟显示时间）
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

  @override
  Widget build(BuildContext context) {
    final title = _members.isEmpty ? _name : '$_name(${_members.length})';
    final entries = _entries;
    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          icon: const WcBackIcon(size: 24),
          onPressed: () => Navigator.of(context).maybePop(),
        ),
        title: Text(title.isEmpty ? '群聊' : title),
        actions: [
          IconButton(icon: const WcMoreIcon(size: 24), tooltip: '群聊信息', onPressed: _openSettings),
          const SizedBox(width: 4),
        ],
      ),
      body: Column(children: [
        Expanded(
          child: Stack(
            children: [
              Positioned.fill(
                child: ListView.builder(
                  controller: _scroll,
                  reverse: true,
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  itemCount: entries.length,
                  itemBuilder: (ctx, i) {
                    final e = entries[entries.length - 1 - i];
                    if (e is double) return _timeSeparator(e);
                    return _bubble(e as Map<String, dynamic>);
                  },
                ),
              ),
              Positioned(top: 8, right: 12, child: _newMsgPill()),
            ],
          ),
        ),
        if (_syncFailed) _offlineBar(),
        _inputBar(),
      ]),
    );
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

  Widget _offlineBar() {
    return InkWell(
      onTap: () {
        setState(() => _syncFailed = false);
        _load();
      },
      child: Container(
        width: double.infinity,
        color: const Color(0xFFFFF3E0),
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: const Text('网络异常，点击重试',
            textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Color(0xFFB26A00))),
      ),
    );
  }

  Widget _inputBar() {
    final hasText = _hasText;
    return Container(
      color: const Color(0xFFF7F7F7),
      child: SafeArea(
        top: false,
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          if (_showPlus) _plusPanel(),
          if (_showEmoji)
            EmojiPanel(
              controller: _input,
              onChanged: () {
                final h = _input.text.trim().isNotEmpty;
                if (h != _hasText) setState(() => _hasText = h);
              },
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(10, 7, 10, 7),
            child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
              Expanded(
                child: Container(
                  constraints: const BoxConstraints(minHeight: 40),
                  decoration:
                      BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(6)),
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
                  child: TextField(
                    controller: _input,
                    maxLines: 4,
                    minLines: 1,
                    style: const TextStyle(fontSize: 16),
                    decoration: const InputDecoration(
                      isDense: true,
                      border: InputBorder.none,
                      contentPadding: EdgeInsets.zero,
                    ),
                    onChanged: (_) {
                      // 只在“有无内容”变化时重建，减少整页 setState
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
              ),
              const SizedBox(width: 10),
              GestureDetector(
                onTap: () => setState(() {
                  FocusManager.instance.primaryFocus?.unfocus();
                  _showEmoji = !_showEmoji;
                  if (_showEmoji) _showPlus = false;
                }),
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
                  onTap: () => setState(() {
                    FocusManager.instance.primaryFocus?.unfocus();
                    _showPlus = !_showPlus;
                    if (_showPlus) _showEmoji = false;
                  }),
                  child: const Padding(
                    padding: EdgeInsets.all(6),
                    child: WcPlusCircleIcon(size: 30, color: Color(0xFF2C2C2C)),
                  ),
                ),
            ]),
          ),
        ]),
      ),
    );
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

  Widget _plusItem(IconData icon, String label, VoidCallback onTap) {
    return InkWell(
      onTap: onTap,
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Container(
          width: 60,
          height: 60,
          decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(14)),
          child: Icon(icon, size: 28, color: const Color(0xFF555555)),
        ),
        const SizedBox(height: 6),
        Text(label, style: const TextStyle(fontSize: 12, color: Color(0xFF666666))),
      ]),
    );
  }

  Widget _bubble(Map<String, dynamic> m) {
    final isUser = m['role'] == 'user';
    final kind = m['kind'] ?? 'text';
    final name = '${m['name'] ?? ''}';
    Widget body;
    if (kind == 'sticker') {
      body = ClipRRect(
        borderRadius: BorderRadius.circular(6),
        child: Image.network(fullUrl('${m['media_url']}'),
            width: 110,
            cacheWidth: 240,
            errorBuilder: (_, _, _) => const Text('[表情]', style: TextStyle(color: Colors.grey))),
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
              if (p == null) return w;
              return Container(
                width: 180,
                height: 140,
                color: Colors.black12,
                child: const Center(
                    child: SizedBox(
                        width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))),
              );
            },
            errorBuilder: (_, _, _) => Container(
              width: 180,
              height: 100,
              color: Colors.black12,
              child: const Center(
                  child: Text('[图片加载失败]', style: TextStyle(fontSize: 12, color: Colors.grey))),
            ),
          ),
        ),
      );
      final caption = kind == 'image' ? cleanMetaTime('${m['content'] ?? ''}') : '';
      if (caption.isNotEmpty) {
        body = Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [body, const SizedBox(height: 5), Text(caption, style: const TextStyle(fontSize: 15))],
        );
      }
    } else if (kind == 'video') {
      body = GestureDetector(
        onTap: () => showVideoViewer(context, fullUrl('${m['media_url']}')),
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
            ]),
          ),
        ),
      );
    } else if (kind == 'voice') {
      body = Text('[语音] ${cleanMetaTime('${m['content'] ?? ''}')}',
          style: const TextStyle(fontSize: 15));
    } else {
      body = Text(cleanMetaTime('${m['content'] ?? ''}'),
          style: const TextStyle(fontSize: 16, height: 1.35, color: Colors.black));
    }

    final bubble = Container(
      padding:
          kind == 'sticker' ? EdgeInsets.zero : const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: kind == 'sticker'
          ? null
          : BoxDecoration(
              color: isUser ? kWeChatGreen : Colors.white,
              borderRadius: BorderRadius.circular(6),
            ),
      child: body,
    );

    final wrapped = GestureDetector(
      onLongPressStart: kind == 'text' ? (d) => _showMenu(m, d.globalPosition) : null,
      child: bubble,
    );

    Widget content = wrapped;
    if (!isUser) {
      content = Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.only(bottom: 3, left: 2),
          child: Text(name.isEmpty ? '角色' : name,
              style: const TextStyle(fontSize: 12, color: Color(0xFF999999))),
        ),
        wrapped,
      ]);
    }

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
        children: [
          if (!isUser) DefaultAvatar(url: _memberAvatar(m), size: 40),
          if (!isUser) const SizedBox(width: 8),
          if (!isUser && kind != 'sticker') const BubbleTail(left: true, color: Colors.white),
          Flexible(
            child: ConstrainedBox(
              constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.62),
              child: content,
            ),
          ),
          if (isUser && kind != 'sticker') const BubbleTail(left: false, color: kWeChatGreen),
          if (isUser) const SizedBox(width: 8),
          if (isUser) DefaultAvatar(url: _userAvatar, size: 40),
        ],
      ),
    );
  }
}
