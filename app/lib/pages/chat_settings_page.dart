import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api.dart';
import '../local_db.dart';
import '../util.dart';
import 'character_edit_page.dart';
import 'memory_page.dart';

class ChatSettingsPage extends StatefulWidget {
  final int cid;
  final String name;
  final String? avatar;
  const ChatSettingsPage({super.key, required this.cid, required this.name, this.avatar});

  @override
  State<ChatSettingsPage> createState() => _ChatSettingsPageState();
}

class _ChatSettingsPageState extends State<ChatSettingsPage> {
  bool _photo = false;
  bool _voice = false;
  bool _sticker = true;
  bool _video = false;
  bool _imgDesc = true;
  bool _voiceDesc = true;
  bool _wake = true;
  bool _reminder = false;
  bool _momentsAuto = false;
  bool _momentsReact = true;
  String _micMode = 'send';
  String _bg = '';
  List<Map<String, dynamic>> _backgrounds = [];
  List<Map<String, dynamic>> _kbDocs = [];
  bool _loading = true;


  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await Api.characterGet(widget.cid);
      final chat = (d['chat'] as Map?)?.cast<String, dynamic>() ?? {};
      final bg = await Api.backgrounds();
      final kb = await Api.kbList(widget.cid);
      String mic = 'send';
      try {
        final prefs = await SharedPreferences.getInstance();
        mic = prefs.getString('mic_mode_${widget.cid}') ?? 'send';
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _photo = chat['photo_enabled'] == true;
        _voice = chat['voice_enabled'] == true;
        _sticker = chat['sticker_enabled'] == true;
        _video = chat['video_enabled'] == true;
        _imgDesc = chat['img_desc_enabled'] != false;
        _voiceDesc = chat['voice_desc_enabled'] != false;
        _wake = chat['wake_enabled'] != false;
        _reminder = chat['reminder_enabled'] == true;
        _momentsAuto = chat['moments_auto'] == true;
        _momentsReact = chat['moments_react'] != false;
        _micMode = mic;
        _bg = (chat['background'] ?? '') as String;
        _backgrounds = ((bg['backgrounds'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
        _kbDocs = ((kb['character'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _setSwitch(
      {bool? photo,
      bool? voice,
      bool? sticker,
      bool? video,
      bool? imgDesc,
      bool? voiceDesc,
      bool? wake,
      bool? reminder,
      bool? momentsAuto,
      bool? momentsReact}) async {
    final old = {
      'photo': _photo,
      'voice': _voice,
      'sticker': _sticker,
      'video': _video,
      'imgDesc': _imgDesc,
      'voiceDesc': _voiceDesc,
      'wake': _wake,
      'reminder': _reminder,
      'momentsAuto': _momentsAuto,
      'momentsReact': _momentsReact,
    };
    setState(() {
      if (photo != null) _photo = photo;
      if (voice != null) _voice = voice;
      if (sticker != null) _sticker = sticker;
      if (video != null) _video = video;
      if (imgDesc != null) _imgDesc = imgDesc;
      if (voiceDesc != null) _voiceDesc = voiceDesc;
      if (wake != null) _wake = wake;
      if (reminder != null) _reminder = reminder;
      if (momentsAuto != null) _momentsAuto = momentsAuto;
      if (momentsReact != null) _momentsReact = momentsReact;
    });
    try {
      await Api.chatSettings(widget.cid,
          photo: photo,
          voice: voice,
          sticker: sticker,
          video: video,
          imgDesc: imgDesc,
          voiceDesc: voiceDesc,
          wake: wake,
          reminder: reminder,
          momentsAuto: momentsAuto,
          momentsReact: momentsReact);
      await LocalDb.updateChatJson(widget.cid, {
        'photo_enabled': _photo,
        'voice_enabled': _voice,
        'sticker_enabled': _sticker,
        'video_enabled': _video,
        'img_desc_enabled': _imgDesc,
        'voice_desc_enabled': _voiceDesc,
        'wake_enabled': _wake,
        'reminder_enabled': _reminder,
        'moments_auto': _momentsAuto,
        'moments_react': _momentsReact,
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          // 失败回滚 UI，避免与服务端状态不一致
          _photo = old['photo']!;
          _voice = old['voice']!;
          _sticker = old['sticker']!;
          _video = old['video']!;
          _imgDesc = old['imgDesc']!;
          _voiceDesc = old['voiceDesc']!;
          _wake = old['wake']!;
          _reminder = old['reminder']!;
          _momentsAuto = old['momentsAuto']!;
          _momentsReact = old['momentsReact']!;
        });
        showToast(context, '设置失败');
      }
    }
  }

  Future<void> _setBg(String url) async {
    final old = _bg;
    setState(() => _bg = url);
    try {
      await Api.chatSettings(widget.cid, background: url);
      await LocalDb.updateChatJson(widget.cid, {'background': url});
    } catch (_) {
      if (mounted) {
        setState(() => _bg = old); // 失败回滚，避免本地与服务端不一致
        showToast(context, '背景设置失败');
      }
    }
  }

  Future<void> _uploadKb() async {
    final res = await FilePicker.platform.pickFiles(type: FileType.custom, allowedExtensions: ['md', 'txt', 'markdown']);
    if (res == null || res.files.isEmpty) return;
    final p = res.files.first.path;
    if (p == null) return;
    final f = File(p);
    int fsize = 0;
    try {
      fsize = f.lengthSync();
    } catch (_) {}
    if (fsize > 5 * 1024 * 1024) {
      if (mounted) showToast(context, '文件不能超过 5MB');
      return;
    }
    if (mounted) showToast(context, '上传中，正在向量化…');
    try {
      final r = await Api.kbUpload(widget.cid, f);
      if (mounted) showToast(context, (r['ok'] == true) ? '上传成功（${r['chunks']} 块）' : '上传失败');
    } catch (_) {
      if (mounted) showToast(context, '上传失败');
    }
    _load();
  }

  Future<void> _pickMicMode() async {
    final v = await showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            title: const Text('语音发送', style: TextStyle(fontSize: 16)),
            subtitle: const Text('识别后作为语音消息发送给她', style: TextStyle(fontSize: 12, color: Colors.grey)),
            trailing: _micMode == 'send' ? const Icon(Icons.check, color: kWeChatTheme) : null,
            onTap: () => Navigator.pop(ctx, 'send'),
          ),
          ListTile(
            title: const Text('转文字输入', style: TextStyle(fontSize: 16)),
            subtitle: const Text('识别成文字填进输入框，确认后再发送', style: TextStyle(fontSize: 12, color: Colors.grey)),
            trailing: _micMode == 'input' ? const Icon(Icons.check, color: kWeChatTheme) : null,
            onTap: () => Navigator.pop(ctx, 'input'),
          ),
          const SizedBox(height: 6),
        ]),
      ),
    );
    if (v == null) return;
    _micMode = v;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('mic_mode_${widget.cid}', v);
    } catch (_) {}
    if (mounted) setState(() {});
  }

  Future<void> _clearHistory() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('清空聊天记录'),
        content: const Text('确定清空与该角色的全部聊天记录吗？此操作不可恢复。'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('取消', style: TextStyle(color: Colors.black))),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('清空', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      final r = await Api.reset(widget.cid);
      final greetId = (r['greeting_id'] as num?)?.toInt() ?? 0;
      await LocalDb.clearMessages(widget.cid);
      await LocalDb.setDraft(widget.cid, ''); // 清空聊天同时清草稿
      if (greetId > 0) await LocalDb.setLastRead(widget.cid, greetId); // 开场白不算未读
      if (mounted) showToast(context, '已清空');
    } catch (_) {
      if (mounted) showToast(context, '清空失败');
    }
  }

  Future<void> _showCheatDialog() async {
    final ctrl = TextEditingController();
    await showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('作弊器'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('口述你的指令，比如「凭空生成一束玫瑰」「让她换个发型」',
                style: TextStyle(fontSize: 12, color: Colors.grey)),
            const SizedBox(height: 8),
            TextField(
              controller: ctrl,
              autofocus: true,
              decoration: const InputDecoration(hintText: '输入指令…'),
              onSubmitted: (v) async {
                Navigator.pop(ctx);
                await _doCheat(v);
              },
            ),
          ],
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('取消', style: TextStyle(color: Colors.black))),
          TextButton(
            onPressed: () async {
              final v = ctrl.text;
              Navigator.pop(ctx);
              await _doCheat(v);
            },
            child: const Text('执行'),
          ),
        ],
      ),
    );
    ctrl.dispose();
  }

  Future<void> _doCheat(String cmd) async {
    if (cmd.trim().isEmpty) return;
    try {
      final r = await Api.cheat(widget.cid, cmd.trim());
      if (mounted) {
        showToast(
            context, r['ok'] == true ? '${r['msg'] ?? '已执行'}' : '${r['error'] ?? '执行失败'}');
      }
    } catch (_) {
      if (mounted) showToast(context, '作弊器调用失败（网络错误）');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('聊天信息')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              children: [
                const SizedBox(height: 10),
                _section([
                  ListTile(
                    leading: DefaultAvatar(url: _avatarUrl(widget.avatar), size: 46),
                    title: Text(widget.name),
                    trailing: const Icon(Icons.chevron_right, color: Colors.grey),
                    onTap: () async {
                      final r = await Navigator.push(
                          context,
                          MaterialPageRoute(
                              builder: (_) => CharacterEditPage(cid: widget.cid, name: widget.name)));
                      if (r == true) _load();
                    },
                  ),
                ]),
                const SizedBox(height: 10),
                _section([
                  SwitchListTile(
                    value: _photo,
                    onChanged: (v) => _setSwitch(photo: v),
                    title: const Text('照片生成'),
                    subtitle: const Text('开启后，角色会在合适时发来生成的照片', style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                  SwitchListTile(
                    value: _voice,
                    onChanged: (v) => _setSwitch(voice: v),
                    title: const Text('语音'),
                    subtitle: const Text('开启后，角色会在情绪饱满时发语音', style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                  SwitchListTile(
                    value: _sticker,
                    onChanged: (v) => _setSwitch(sticker: v),
                    title: const Text('表情包'),
                    subtitle: const Text('开启后，角色会在合适时发表情', style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                  SwitchListTile(
                    value: _video,
                    onChanged: (v) => _setSwitch(video: v),
                    title: const Text('视频'),
                    subtitle: const Text('开启后，角色会在特别时刻发短视频（生成约 1~3 分钟）', style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                  SwitchListTile(
                    value: _imgDesc,
                    onChanged: (v) => _setSwitch(imgDesc: v),
                    title: const Text('图片转述'),
                    subtitle: const Text('开启：你发的图片先转成文字再交给她的模型（适合纯文本模型）；关闭：原图直接交给多模态模型看图', style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                  SwitchListTile(
                    value: _voiceDesc,
                    onChanged: (v) => _setSwitch(voiceDesc: v),
                    title: const Text('语音转述'),
                    subtitle: const Text('开启：你的语音先转成文字再交给她；关闭：录音不识别，原音频直接交给多模态模型听', style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                  ListTile(
                    title: const Text('语音用途'),
                    subtitle: Text(
                        _micMode == 'send' ? '按住说话：识别后作为语音消息发送' : '按住说话：识别成文字，确认后再发送',
                        style: const TextStyle(fontSize: 12, color: Colors.grey)),
                    trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                      Text(_micMode == 'send' ? '语音发送' : '转文字输入',
                          style: const TextStyle(fontSize: 14, color: Color(0xFF999999))),
                      const Icon(Icons.chevron_right, size: 18, color: Color(0xFFC7C7C7)),
                    ]),
                    onTap: _pickMicMode,
                  ),
                ]),
                const SizedBox(height: 10),
                _section([
                  SwitchListTile(
                    value: _wake,
                    onChanged: (v) => _setSwitch(wake: v),
                    title: const Text('主动联系'),
                    subtitle: const Text('开启后，久未聊天她会主动来找你', style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                  SwitchListTile(
                    value: _reminder,
                    onChanged: (v) => _setSwitch(reminder: v),
                    title: const Text('主动关心'),
                    subtitle: const Text('开启后她更容易主动关心你、撒娇', style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                  SwitchListTile(
                    value: _momentsAuto,
                    onChanged: (v) => _setSwitch(momentsAuto: v),
                    title: const Text('自动发朋友圈'),
                    subtitle: const Text('开启后，她会像真人一样自己发动态', style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                  SwitchListTile(
                    value: _momentsReact,
                    onChanged: (v) => _setSwitch(momentsReact: v),
                    title: const Text('朋友圈互动'),
                    subtitle: const Text('你发动态时她会点赞/评论', style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                ]),
                const SizedBox(height: 10),
                _section([
                  ListTile(
                    leading: const Icon(Icons.search, color: Color(0xFF555555)),
                    title: const Text('查找聊天记录', style: TextStyle(fontSize: 15.5)),
                    trailing: const Icon(Icons.chevron_right, size: 18, color: Color(0xFFC7C7C7)),
                    onTap: () => showToast(context, '活动未开放'),
                  ),
                  ListTile(
                    leading: const Icon(Icons.auto_fix_high, color: Color(0xFF555555)),
                    title: const Text('作弊器（剧情注入）', style: TextStyle(fontSize: 15.5)),
                    trailing: const Icon(Icons.chevron_right, size: 18, color: Color(0xFFC7C7C7)),
                    onTap: _showCheatDialog,
                  ),
                  ListTile(
                    leading: const Icon(Icons.psychology_alt_outlined, color: Color(0xFF555555)),
                    title: const Text('她的回忆（长期记忆）', style: TextStyle(fontSize: 15.5)),
                    subtitle: const Text('她自动记住的事，可置顶/纠正/删除',
                        style: TextStyle(fontSize: 12, color: Colors.grey)),
                    trailing: const Icon(Icons.chevron_right, size: 18, color: Color(0xFFC7C7C7)),
                    onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute(
                            builder: (_) => MemoryPage(cid: widget.cid, name: widget.name))),
                  ),
                ]),
                const SizedBox(height: 10),
                _sectionHeader('聊天背景'),
                _section([
                  Padding(
                    padding: const EdgeInsets.all(12),
                    child: Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        _bgItem('', '默认'),
                        for (final b in _backgrounds) _bgItem((b['url'] ?? '') as String, (b['name'] ?? '') as String),
                      ],
                    ),
                  ),
                ]),
                const SizedBox(height: 10),
                _sectionHeader('知识库（角色专属）'),
                _section([
                  if (_kbDocs.isEmpty)
                    const Padding(
                      padding: EdgeInsets.all(14),
                      child: Text('还没有知识库文档，点下方上传 md/txt', style: TextStyle(color: Colors.grey, fontSize: 13)),
                    )
                  else
                    for (final d in _kbDocs)
                      ListTile(
                        dense: true,
                        title: Text((d['filename'] ?? '') as String, style: const TextStyle(fontSize: 14)),
                        subtitle: Text(_statusText(d), style: TextStyle(fontSize: 12, color: _statusColor(d))),
                        trailing: Text('${d['chunk_count'] ?? 0} 块', style: const TextStyle(fontSize: 12, color: Colors.grey)),
                      ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
                    child: SizedBox(
                      width: double.infinity,
                      child: OutlinedButton.icon(
                        onPressed: _uploadKb,
                        icon: const Icon(Icons.upload_file, size: 18),
                        label: const Text('上传知识库（md / txt）'),
                      ),
                    ),
                  ),
                ]),
                const SizedBox(height: 10),
                _section([
                  ListTile(
                    title: const Text('清空聊天记录', style: TextStyle(color: Colors.red)),
                    onTap: _clearHistory,
                  ),
                ]),
                const SizedBox(height: 30),
              ],
            ),
    );
  }

  String _statusText(Map<String, dynamic> d) {
    switch (d['status']) {
      case 'ready':
        return '已就绪';
      case 'error':
        return '失败：${d['error'] ?? ''}';
      default:
        return '处理中…';
    }
  }

  Color _statusColor(Map<String, dynamic> d) {
    switch (d['status']) {
      case 'ready':
        return const Color(0xFF07C160);
      case 'error':
        return Colors.red;
      default:
        return const Color(0xFFE6A23C);
    }
  }

  Widget _bgItem(String url, String name) {
    final selected = _bg == url;
    Widget inner;
    if (url.isEmpty) {
      inner = Container(
        width: 76,
        height: 52,
        color: kWeChatLight,
        child: const Center(child: Text('默认', style: TextStyle(fontSize: 12, color: Colors.grey))),
      );
    } else {
      inner = Image.network(fullUrl(url),
          width: 76,
          height: 52,
          fit: BoxFit.cover,
          cacheWidth: 160,
          errorBuilder: (_, _, _) => Container(width: 76, height: 52, color: Colors.black12));
    }
    return GestureDetector(
      onTap: () => _setBg(url),
      child: Container(
        decoration: BoxDecoration(
          border: Border.all(color: selected ? kWeChatTheme : Colors.transparent, width: 2),
          borderRadius: BorderRadius.circular(5),
        ),
        child: ClipRRect(borderRadius: BorderRadius.circular(4), child: inner),
      ),
    );
  }

  Widget _section(List<Widget> children) {
    return Container(color: Colors.white, child: Column(children: children));
  }

  Widget _sectionHeader(String title) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 6),
      child: Text(title, style: const TextStyle(fontSize: 13, color: Color(0xFF888888))),
    );
  }
}

String? _avatarUrl(String? a) => avatarFull(a);
