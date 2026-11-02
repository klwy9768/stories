import 'package:flutter/material.dart';

import '../local_db.dart';
import '../util.dart';

/// 选择角色（发起群聊 / 添加群成员）
class GroupCreatePage extends StatefulWidget {
  final String title;
  final List<int> initialSelected;
  const GroupCreatePage({super.key, this.title = '发起群聊', this.initialSelected = const []});

  @override
  State<GroupCreatePage> createState() => _GroupCreatePageState();
}

class _GroupCreatePageState extends State<GroupCreatePage> {
  List<Map<String, dynamic>> _chars = [];
  final Set<int> _sel = {};
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _sel.addAll(widget.initialSelected);
    _load();
  }

  Future<void> _load() async {
    final chars = await LocalDb.allChars();
    if (mounted) {
      setState(() {
        _chars = chars;
        _loading = false;
      });
    }
  }

  String? _avatarUrl(String? a) {
    if (a == null || a.isEmpty) return null;
    if (a.startsWith('http') || a.startsWith('file://')) return a;
    return fullUrl(a);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.title),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, _sel.toList()),
            child: Text('确定${_sel.isEmpty ? '' : '(${_sel.length})'}',
                style: TextStyle(color: _sel.isEmpty ? const Color(0xFF9A9A9A) : kWeChatTheme)),
          ),
          const SizedBox(width: 6),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _chars.isEmpty
              ? const Center(child: Text('还没有角色', style: TextStyle(color: Colors.grey)))
              : ListView(children: [
                  for (final c in _chars) _row(c),
                ]),
    );
  }

  Widget _row(Map<String, dynamic> c) {
    final id = c['id'] as int;
    final checked = _sel.contains(id);
    return ListTile(
      tileColor: Colors.white,
      leading: DefaultAvatar(url: _avatarUrl(c['avatar'] as String?), size: 42),
      title: Text('${c['name'] ?? ''}', style: const TextStyle(fontSize: 16)),
      trailing: Icon(
        checked ? Icons.check_circle : Icons.radio_button_unchecked,
        size: 24,
        color: checked ? kWeChatTheme : const Color(0xFFC7C7C7),
      ),
      onTap: () => setState(() {
        if (checked) {
          _sel.remove(id);
        } else {
          _sel.add(id);
        }
      }),
    );
  }
}
