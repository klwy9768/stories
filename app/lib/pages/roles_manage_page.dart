import 'package:flutter/material.dart';

import '../api.dart';
import '../local_db.dart';
import '../util.dart';
import 'character_edit_page.dart';

/// 角色管理（通讯录 → 标签）：集中编辑 / 启用停用 / 删除角色
class RolesManagePage extends StatefulWidget {
  const RolesManagePage({super.key});

  @override
  State<RolesManagePage> createState() => _RolesManagePageState();
}

class _RolesManagePageState extends State<RolesManagePage> {
  List<Map<String, dynamic>> _chars = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final chars = await LocalDb.allChars();
    if (mounted) setState(() => _chars = chars);
  }

  Future<void> _toggle(Map<String, dynamic> c, bool v) async {
    final cid = c['id'];
    if (cid is! int) return;
    setState(() => c['enabled'] = v ? 1 : 0); // 乐观更新，开关立即可见
    try {
      await Api.characterSave({'id': cid, 'enabled': v});
      await LocalDb.setCharEnabled(cid, v);
    } catch (_) {
      if (mounted) {
        setState(() => c['enabled'] = v ? 0 : 1); // 失败回滚
        showToast(context, '设置失败');
      }
    }
  }

  Future<void> _edit(Map<String, dynamic>? c) async {
    await Navigator.push(
      context,
      MaterialPageRoute(
          builder: (_) => CharacterEditPage(cid: c?['id'] as int?, name: c?['name'] as String?)),
    );
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('角色管理'),
        actions: [
          IconButton(icon: const Icon(Icons.person_add_alt_1), tooltip: '新建角色', onPressed: () => _edit(null)),
          const SizedBox(width: 4),
        ],
      ),
      body: ListView.separated(
        itemCount: _chars.length,
        separatorBuilder: (_, _) => const Divider(indent: 68),
        itemBuilder: (ctx, i) {
          final c = _chars[i];
          return ListTile(
            tileColor: Colors.white,
            leading: DefaultAvatar(url: avatarFull(c['avatar'] as String?), size: 44),
            title: Text('${c['name'] ?? ''}', style: const TextStyle(fontSize: 16)),
            subtitle: Text(cleanMetaTime('${c['last_content'] ?? '暂无聊天'}'),
                maxLines: 1, overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12, color: Color(0xFF999999))),
            trailing: Row(mainAxisSize: MainAxisSize.min, children: [
              Switch(
                value: c['enabled'] != 0,
                activeThumbColor: kWeChatTheme,
                onChanged: (v) => _toggle(c, v),
              ),
              const Icon(Icons.chevron_right, size: 20, color: Color(0xFFC7C7C7)),
            ]),
            onTap: () => _edit(c),
          );
        },
      ),
    );
  }
}
