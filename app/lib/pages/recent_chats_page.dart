import 'package:flutter/material.dart';

import '../local_db.dart';
import '../util.dart';
import 'chat_page.dart';

/// 仅聊天的朋友（按最近聊天时间排序的常聊列表）
class RecentChatsPage extends StatefulWidget {
  const RecentChatsPage({super.key});

  @override
  State<RecentChatsPage> createState() => _RecentChatsPageState();
}

class _RecentChatsPageState extends State<RecentChatsPage> {
  List<Map<String, dynamic>> _chars = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final chars = await LocalDb.allChars();
    chars.sort((a, b) => ((b['last_ts'] as num?)?.toDouble() ?? 0)
        .compareTo((a['last_ts'] as num?)?.toDouble() ?? 0));
    if (mounted) setState(() => _chars = chars);
  }

  Future<void> _open(Map<String, dynamic> c) async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => ChatPage(cid: c['id'] as int)));
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('仅聊天的朋友')),
      body: _chars.isEmpty
          ? const Center(child: Text('还没有聊过天的朋友', style: TextStyle(color: Colors.grey)))
          : ListView.separated(
              itemCount: _chars.length,
              separatorBuilder: (_, _) => const Divider(indent: 68),
              itemBuilder: (ctx, i) {
                final c = _chars[i];
                return InkWell(
                  onTap: () => _open(c),
                  child: Container(
                    color: Colors.white,
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    child: Row(children: [
                      DefaultAvatar(url: avatarFull(c['avatar'] as String?), size: 44),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Text('${c['name'] ?? ''}',
                              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w500),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis),
                          const SizedBox(height: 3),
                          Text(cleanMetaTime('${c['last_content'] ?? ''}'),
                              style: const TextStyle(fontSize: 13, color: Color(0xFF999999)),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis),
                        ]),
                      ),
                      const SizedBox(width: 8),
                      Text(fmtTime((c['last_ts'] as num?)?.toDouble() ?? 0),
                          style: const TextStyle(fontSize: 11, color: Color(0xFFB2B2B2))),
                    ]),
                  ),
                );
              },
            ),
    );
  }
}
