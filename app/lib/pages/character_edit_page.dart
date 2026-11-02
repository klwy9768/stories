import 'dart:io';

import 'package:flutter/material.dart';

import '../api.dart';
import '../local_db.dart';
import '../pick.dart';
import '../util.dart';

class CharacterEditPage extends StatefulWidget {
  final int? cid;
  final String? name;
  const CharacterEditPage({super.key, this.cid, this.name});

  @override
  State<CharacterEditPage> createState() => _CharacterEditPageState();
}

class _CharacterEditPageState extends State<CharacterEditPage> {
  final _name = TextEditingController();
  final _desc = TextEditingController();
  final _greeting = TextEditingController();
  final _nickname = TextEditingController();
  final _personality = TextEditingController();
  final _style = TextEditingController();
  final _habits = TextEditingController();
  final _taboo = TextEditingController();
  final _persona = TextEditingController();
  final _story = TextEditingController();

  String? _avatarUrl;
  File? _avatarFile;
  bool _loading = false;
  bool _saving = false;
  bool _loadFailed = false; // 编辑模式加载失败：禁止保存，防止空表单把角色资料覆盖掉
  // 后台可能设置过这些字段：编辑时原样带回，避免 App 保存把它们重置为默认值
  String _model = '';
  double _temperature = 0.9;
  int _sortOrder = 0;
  int _enabled = 1;

  bool get isEdit => widget.cid != null;

  @override
  void initState() {
    super.initState();
    if (widget.name != null) _name.text = widget.name!;
    if (isEdit) _load();
  }

  @override
  void dispose() {
    for (final c in [_name, _desc, _greeting, _nickname, _personality, _style, _habits, _taboo, _persona, _story]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final d = await Api.characterGet(widget.cid!);
      if (!mounted) return;
      final f = (d['fields'] as Map?)?.cast<String, dynamic>() ?? {};
      setState(() {
        _name.text = (d['name'] ?? '') as String;
        _desc.text = (d['description'] ?? '') as String;
        _greeting.text = (d['greeting'] ?? '') as String;
        _nickname.text = (f['nickname'] ?? '') as String;
        _personality.text = (f['personality'] ?? '') as String;
        _style.text = (f['style'] ?? '') as String;
        _habits.text = (f['habits'] ?? '') as String;
        _taboo.text = (f['taboo'] ?? '') as String;
        _persona.text = (d['persona_full'] ?? '') as String;
        _story.text = (d['story'] ?? '') as String;
        _avatarUrl = (d['avatar'] ?? '') as String;
        _model = (d['model'] ?? '') as String;
        _temperature = (d['temperature'] as num?)?.toDouble() ?? 0.9;
        _sortOrder = (d['sort_order'] as num?)?.toInt() ?? 0;
        _enabled = (d['enabled'] as num?)?.toInt() ?? 1;
      });
    } catch (_) {
      _loadFailed = true;
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _pickAvatar() async {
    final files = await pickAssets(context, max: 1, type: RequestType.image);
    if (files.isEmpty || !mounted) return;
    setState(() => _avatarFile = files.first);
  }

  Future<void> _save() async {
    if (isEdit && _loadFailed) {
      showToast(context, '资料未加载成功，请返回重进或点重试后再保存');
      return;
    }
    if (_name.text.trim().isEmpty) {
      showToast(context, '请填写名字');
      return;
    }
    setState(() => _saving = true);
    try {
      final body = <String, dynamic>{
        'name': _name.text.trim(),
        'description': _desc.text.trim(),
        'greeting': _greeting.text.trim(),
        'persona_full': _persona.text.trim(),
        'story': _story.text.trim(),
        'fields': {
          'nickname': _nickname.text.trim(),
          'personality': _personality.text.trim(),
          'style': _style.text.trim(),
          'habits': _habits.text.trim(),
          'taboo': _taboo.text.trim(),
        },
      };
      if (isEdit) {
        body['id'] = widget.cid;
        body['model'] = _model;
        body['temperature'] = _temperature;
        body['sort_order'] = _sortOrder;
        body['enabled'] = _enabled;
      }
      final r = await Api.characterSave(body);
      final cid = (r['id'] as num?)?.toInt();
      var avatarFailed = false;
      if (_avatarFile != null && cid != null) {
        try {
          await Api.avatarUpload(cid, _avatarFile!);
        } catch (_) {
          avatarFailed = true; // 资料已保存成功，头像失败不阻断（避免用户重试导致重复建角色）
        }
      }
      if (mounted) {
        showToast(context, avatarFailed ? '资料已保存，头像上传失败，可稍后重试' : '已保存');
        Navigator.pop(context, true);
      }
    } catch (_) {
      if (mounted) showToast(context, '保存失败，请检查网络');
    }
    if (mounted) setState(() => _saving = false);
  }

  Future<void> _delete() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('删除角色'),
        content: const Text('将删除该角色的资料、聊天记录与知识库，确定吗？'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('取消', style: TextStyle(color: Colors.black))),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('删除', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await Api.deleteChar(widget.cid!);
      await LocalDb.deleteChar(widget.cid!);
      if (mounted) Navigator.pop(context, true);
    } catch (_) {
      if (mounted) showToast(context, '删除失败');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(isEdit ? '编辑角色' : '新建角色'),
        actions: [
          TextButton(
            onPressed: (_saving || _loadFailed) ? null : _save,
            child: const Text('保存', style: TextStyle(color: kWeChatTheme, fontSize: 16)),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _loadFailed
              ? Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    const Text('资料加载失败，请检查网络', style: TextStyle(color: Color(0xFF888888))),
                    const SizedBox(height: 8),
                    TextButton(
                      onPressed: () {
                        setState(() => _loadFailed = false);
                        _load();
                      },
                      child: const Text('重试'),
                    ),
                  ]),
                )
              : ListView(
              padding: const EdgeInsets.only(bottom: 40),
              children: [
                Container(
                  color: Colors.white,
                  padding: const EdgeInsets.symmetric(vertical: 16),
                  child: Column(
                    children: [
                      GestureDetector(
                        onTap: _pickAvatar,
                        child: _avatarFile != null
                            ? ClipRRect(
                                borderRadius: BorderRadius.circular(8),
                                child: Image.file(_avatarFile!, width: 76, height: 76, fit: BoxFit.cover))
                            : DefaultAvatar(url: fullUrl(_avatarUrl), size: 76),
                      ),
                      const SizedBox(height: 8),
                      const Text('点击更换头像', style: TextStyle(fontSize: 12, color: Colors.grey)),
                      const SizedBox(height: 2),
                      const Text('不上传则使用默认头像；头像与 AI 发来的照片是两回事',
                          style: TextStyle(fontSize: 11, color: Color(0xFF999999))),
                    ],
                  ),
                ),
                const SizedBox(height: 10),
                _field('名字 *', _name, hint: '角色名字', desc: '聊天列表与聊天页顶部显示的名字'),
                _field('简介', _desc, hint: '一句话介绍（可选）', desc: '仅供列表/管理展示，AI 对话时不读取'),
                _field('开场白', _greeting, hint: '打开聊天时角色发的第一句话', desc: '首次进入聊天时角色主动发来的第一句；清空记录后会重新发送'),
                const SizedBox(height: 10),
                _field('对方称呼', _nickname, hint: '如：你 / 主人', desc: '角色平时怎么叫你，AI 会用这个称呼称呼你'),
                _field('性格', _personality, hint: '如：温柔、俏皮、傲娇', desc: '影响 AI 的语气与反应方式'),
                _field('说话风格', _style, hint: '如：短句、爱用颜文字', desc: '影响 AI 的用词与句式习惯'),
                _field('口癖/习惯', _habits, hint: '如：爱说"是吧是吧"', desc: '角色的口头禅或小动作，出现频率由 AI 自然把握'),
                _field('禁忌', _taboo, hint: '角色绝对不做的事（可选）', desc: '角色绝不做的事 / 不聊的话题；留空则不特别限制'),
                const SizedBox(height: 10),
                Container(
                  color: Colors.white,
                  padding: const EdgeInsets.all(14),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('故事背景（世界背景、社会背景等；留空默认「现在」）', style: TextStyle(fontSize: 13, color: Color(0xFF666666))),
                      const SizedBox(height: 4),
                      const Text('角色所处的世界：时代、社会、规则、重要地点与事件；填了它，AI 就会严格按这个世界观来演',
                          style: TextStyle(fontSize: 11, color: Color(0xFF999999))),
                      const SizedBox(height: 8),
                      TextField(
                        controller: _story,
                        maxLines: 8,
                        minLines: 3,
                        style: const TextStyle(fontSize: 14),
                        decoration: const InputDecoration(
                          hintText: '描述这个角色所处的世界：时代、社会、规则、重要地点与事件……AI 会按这个世界观来演',
                          hintStyle: TextStyle(color: Colors.grey, fontSize: 13),
                          border: OutlineInputBorder(),
                        ),
                      ),
                    ],
                  ),
                ),
                Container(
                  color: Colors.white,
                  padding: const EdgeInsets.all(14),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('人设（自由文本，越详细越像）', style: TextStyle(fontSize: 13, color: Color(0xFF666666))),
                      const SizedBox(height: 4),
                      const Text('最核心的设定：身份、经历、与你的关系、说话习惯；会作为 AI 的「人设底稿」，写得越具体，演得越像',
                          style: TextStyle(fontSize: 11, color: Color(0xFF999999))),
                      const SizedBox(height: 8),
                      TextField(
                        controller: _persona,
                        maxLines: 10,
                        minLines: 4,
                        style: const TextStyle(fontSize: 14),
                        decoration: const InputDecoration(
                          hintText: '用一段话描述这个角色：身份、经历、和你的关系、说话习惯……',
                          hintStyle: TextStyle(color: Colors.grey, fontSize: 13),
                          border: OutlineInputBorder(),
                        ),
                      ),
                    ],
                  ),
                ),
                if (isEdit)
                  Container(
                    color: Colors.white,
                    margin: const EdgeInsets.only(top: 10),
                    child: ListTile(
                      title: const Text('删除该角色', style: TextStyle(color: Colors.red)),
                      onTap: _delete,
                    ),
                  ),
              ],
            ),
    );
  }

  Widget _field(String label, TextEditingController c, {String hint = '', String desc = ''}) {
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.fromLTRB(14, 4, 14, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              SizedBox(width: 76, child: Text(label, style: const TextStyle(fontSize: 14))),
              Expanded(
                child: TextField(
                  controller: c,
                  style: const TextStyle(fontSize: 14),
                  decoration: InputDecoration(
                    hintText: hint,
                    hintStyle: const TextStyle(color: Colors.grey, fontSize: 13),
                    border: InputBorder.none,
                  ),
                ),
              ),
            ],
          ),
          if (desc.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(left: 76, top: 1, bottom: 5),
              child: Text(desc, style: const TextStyle(fontSize: 11, color: Color(0xFF999999))),
            ),
        ],
      ),
    );
  }
}
