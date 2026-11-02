import 'package:flutter/material.dart';

import '../api.dart';
import '../log.dart';
import '../util.dart';
import 'group_chat_page.dart';
import 'group_create_page.dart';

/// 群聊列表页（通讯录 → 群聊）
class GroupsPage extends StatefulWidget {
  const GroupsPage({super.key});

  @override
  State<GroupsPage> createState() => _GroupsPageState();
}

class _GroupsPageState extends State<GroupsPage> {
  List<Map<String, dynamic>> _groups = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final g = await Api.groups();
      final list =
          ((g['groups'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
      if (mounted) {
        setState(() {
          _groups = list;
          _loading = false;
        });
      }
    } catch (e) {
      Log.error('group', '加载群列表失败: $e');
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _create() async {
    final ids = await Navigator.push<List<int>>(
        context, MaterialPageRoute(builder: (_) => const GroupCreatePage()));
    if (ids == null || ids.length < 2) return;
    try {
      final r = await Api.groupCreate('', ids);
      if (r['ok'] != true) {
        if (mounted) showToast(context, '${r['error'] ?? '创建失败'}');
        return;
      }
      final gid = (r['id'] as num).toInt();
      if (!mounted) return;
      await Navigator.push(context, MaterialPageRoute(builder: (_) => GroupChatPage(gid: gid)));
      _load();
    } catch (_) {
      if (mounted) showToast(context, '创建失败');
    }
  }

  Future<void> _open(Map<String, dynamic> g) async {
    await Navigator.push(
      context,
      MaterialPageRoute(
          builder: (_) => GroupChatPage(gid: (g['id'] as num).toInt(), name: '${g['name'] ?? ''}')),
    );
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('群聊'),
        actions: [
          IconButton(icon: const Icon(Icons.person_add_alt_1), tooltip: '发起群聊', onPressed: _create),
          const SizedBox(width: 4),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _groups.isEmpty
              ? const Center(
                  child: Text('还没有群聊\n点右上角 ＋ 发起群聊',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: Colors.grey, height: 1.6)))
              : RefreshIndicator(
                  color: kWeChatTheme,
                  onRefresh: _load,
                  child: ListView.separated(
                    physics: const AlwaysScrollableScrollPhysics(),
                    itemCount: _groups.length,
                    separatorBuilder: (_, _) => const Divider(indent: 68),
                    itemBuilder: (ctx, i) {
                      final g = _groups[i];
                      final members = (g['members'] as List?) ?? [];
                      final last = (g['last'] as Map?)?.cast<String, dynamic>();
                      final unread = (g['unread'] as num?)?.toInt() ?? 0;
                      return InkWell(
                        onTap: () => _open(g),
                        child: Container(
                          color: Colors.white,
                          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                          child: Row(children: [
                            Stack(clipBehavior: Clip.none, children: [
                              groupAvatar(members),
                              if (unread > 0)
                                Positioned(
                                  right: -6,
                                  top: -6,
                                  child: Container(
                                    padding:
                                        const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                                    decoration: BoxDecoration(
                                      color: const Color(0xFFFA5151),
                                      borderRadius: BorderRadius.circular(10),
                                    ),
                                    child: Text(unread > 99 ? '99+' : '$unread',
                                        style:
                                            const TextStyle(color: Colors.white, fontSize: 11)),
                                  ),
                                ),
                            ]),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                Text('${g['name'] ?? ''}',
                                    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w500),
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis),
                                const SizedBox(height: 3),
                                Text(
                                    last == null ? '暂无消息' : _groupPrev(last),
                                    style: const TextStyle(fontSize: 13, color: Color(0xFF999999)),
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis),
                              ]),
                            ),
                            const SizedBox(width: 8),
                            Text(fmtTime((last?['ts'] as num?)?.toDouble() ?? 0),
                                style: const TextStyle(fontSize: 11, color: Color(0xFFB2B2B2))),
                          ]),
                        ),
                      );
                    },
                  ),
                ),
    );
  }
}


String _groupPrev(Map<String, dynamic> last) {
  final k = '${last['kind'] ?? 'text'}';
  if (k == 'voice') return '[语音]';
  if (k == 'sticker') return '[表情]';
  if (k == 'image') return '[图片]';
  if (k == 'video') return '[视频]';
  final t = cleanMetaTime('${last['content'] ?? ''}');
  return t.isEmpty ? '（消息）' : t;
}
