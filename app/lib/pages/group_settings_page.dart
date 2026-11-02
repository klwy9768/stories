import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../local_db.dart';
import '../util.dart';
import 'group_create_page.dart';

/// 群聊信息（群名 / 成员 / 主动发言开关 / 删除）
class GroupSettingsPage extends StatefulWidget {
  final int gid;
  const GroupSettingsPage({super.key, required this.gid});

  @override
  State<GroupSettingsPage> createState() => _GroupSettingsPageState();
}

class _GroupSettingsPageState extends State<GroupSettingsPage> {
  String _name = '';
  bool _auto = true;
  List<Map<String, dynamic>> _members = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await Api.groupMessages(widget.gid, 0);
      if (!mounted) return;
      setState(() {
        _name = '${d['name'] ?? ''}';
        _auto = d['auto'] != false;
        _members =
            ((d['members'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  String? _avatar(String? a) {
    if (a == null || a.isEmpty) return null;
    if (a.startsWith('http') || a.startsWith('file://')) return a;
    return fullUrl(a);
  }

  Future<void> _editName() async {
    final ctrl = TextEditingController(text: _name);
    final v = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('修改群名'),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          maxLength: 30,
          decoration: const InputDecoration(hintText: '输入群聊名称'),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('取消', style: TextStyle(color: Colors.black))),
          TextButton(onPressed: () => Navigator.pop(ctx, ctrl.text), child: const Text('保存')),
        ],
      ),
    );
    ctrl.dispose();
    if (v == null) return;
    try {
      await Api.groupSettings(widget.gid, name: v.trim());
      _name = v.trim();
      if (mounted) setState(() {});
    } catch (_) {
      if (mounted) showToast(context, '保存失败');
    }
  }

  Future<void> _editMembers() async {
    final ids = _members.map((m) => m['id'] as int).toList();
    final r = await Navigator.push<List<int>>(
      context,
      MaterialPageRoute(
          builder: (_) => GroupCreatePage(title: '添加成员', initialSelected: ids)),
    );
    if (r == null) return;
    if (r.length < 2) {
      if (mounted) showToast(context, '群聊至少需要 2 个角色');
      return;
    }
    try {
      await Api.groupSettings(widget.gid, cids: r);
      await _load();
    } catch (_) {
      if (mounted) showToast(context, '保存失败');
    }
  }

  Future<void> _toggleAuto(bool v) async {
    setState(() => _auto = v);
    try {
      await Api.groupSettings(widget.gid, auto: v);
    } catch (_) {
      if (mounted) {
        setState(() => _auto = !v); // 失败回滚开关
        showToast(context, '保存失败');
      }
    }
  }

  Future<void> _delete() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除群聊'),
        content: const Text('将删除该群聊及其全部聊天记录，确定吗？'),
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
      await Api.groupSettings(widget.gid, delete: true);
      unawaited(LocalDb.deleteGroup(widget.gid)); // 同步清本地群消息缓存
      if (mounted) Navigator.pop(context, true);
    } catch (_) {
      if (mounted) showToast(context, '删除失败');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('群聊信息')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(children: [
              Container(
                color: Colors.white,
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                child: Wrap(spacing: 16, runSpacing: 12, children: [
                  for (final m in _members)
                    SizedBox(
                      width: 56,
                      child: Column(children: [
                        DefaultAvatar(url: _avatar(m['avatar'] as String?), size: 46),
                        const SizedBox(height: 4),
                        Text('${m['name'] ?? ''}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontSize: 12)),
                      ]),
                    ),
                  GestureDetector(
                    onTap: _editMembers,
                    child: SizedBox(
                      width: 56,
                      child: Column(children: [
                        Container(
                          width: 46,
                          height: 46,
                          decoration: BoxDecoration(
                              border: Border.all(color: const Color(0xFFDDDDDD)),
                              borderRadius: BorderRadius.circular(6)),
                          child: const Icon(Icons.add, size: 26, color: Color(0xFF888888)),
                        ),
                        const SizedBox(height: 4),
                        const Text('添加', style: TextStyle(fontSize: 12)),
                      ]),
                    ),
                  ),
                ]),
              ),
              const SizedBox(height: 10),
              Container(
                color: Colors.white,
                child: Column(children: [
                  ListTile(
                    title: const Text('群聊名称', style: TextStyle(fontSize: 16)),
                    trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                      Text(_name, style: const TextStyle(fontSize: 15, color: Color(0xFF999999))),
                      const Icon(Icons.chevron_right, size: 18, color: Color(0xFFC7C7C7)),
                    ]),
                    onTap: _editName,
                  ),
                  SwitchListTile(
                    value: _auto,
                    onChanged: _toggleAuto,
                    title: const Text('角色主动发言', style: TextStyle(fontSize: 16)),
                    subtitle: const Text('角色会偶尔主动说话、互相接话（有频率、轮数与每日上限，控制消耗）',
                        style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                ]),
              ),
              const SizedBox(height: 10),
              Container(
                color: Colors.white,
                child: ListTile(
                  title: const Text('删除群聊', style: TextStyle(fontSize: 16, color: Colors.red)),
                  onTap: _delete,
                ),
              ),
              const SizedBox(height: 20),
            ]),
    );
  }
}
