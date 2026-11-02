import 'dart:async';

import 'package:flutter/material.dart';

import '../local_db.dart';
import '../util.dart';
import 'chat_page.dart';

/// 微信风搜索页：联系人（名字/最近消息）+ 本地聊天记录
class SearchPage extends StatefulWidget {
  final List<Map<String, dynamic>> chars;
  const SearchPage({super.key, required this.chars});

  @override
  State<SearchPage> createState() => _SearchPageState();
}

class _SearchPageState extends State<SearchPage> {
  final _q = TextEditingController();
  List<Map<String, dynamic>> _msgHits = []; // 只保留命中的历史消息（不再把整表复制进页面内存）
  Timer? _deb; // 输入防抖：避免每敲一个字全量扫描
  String _qText = '';

  Future<void> _runSearch(String q) async {
    try {
      final cids = <int>[];
      for (final c in widget.chars) {
        final cid = (c['id'] as num?)?.toInt();
        if (cid != null) cids.add(cid);
      }
      final hits = await LocalDb.searchMessages(q, cids, limit: 60);
      if (mounted && _qText == q) setState(() => _msgHits = hits);
    } catch (_) {}
  }

  @override
  void dispose() {
    _deb?.cancel();
    _q.dispose();
    super.dispose();
  }

  void _open(int cid) {
    Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => ChatPage(cid: cid)));
  }

  Widget _charTile(Map<String, dynamic> c) {
    return ListTile(
      leading: DefaultAvatar(url: avatarFull(c['avatar'] as String?), size: 44),
      title: Text('${c['name'] ?? ''}', maxLines: 1),
      subtitle: Text(cleanMetaTime('${c['last_content'] ?? ''}'),
          maxLines: 1, style: const TextStyle(fontSize: 12, color: Colors.grey)),
      onTap: () => _open(c['id'] as int),
    );
  }

  List<Widget> _buildHits(String q) {
    final tiles = <Widget>[];
    // 1) 联系人名字命中
    final nameHits = widget.chars
        .where((c) => '${c['name'] ?? ''}'.toLowerCase().contains(q))
        .toList();
    for (final c in nameHits) {
      tiles.add(_charTile(c));
    }
    // 2) 最近消息预览命中（名字未命中时才补充）
    final nameIds = nameHits.map((c) => c['id']).toSet();
    for (final c in widget.chars) {
      if (nameIds.contains(c['id'])) continue;
      if ('${c['last_content'] ?? ''}'.toLowerCase().contains(q)) {
        tiles.add(_charTile(c));
      }
    }
    // 3) 历史聊天记录命中（由 LocalDb.searchMessages 过滤 + 限 60 条，页面不再整表存消息）
    final byId = <int, Map<String, dynamic>>{};
    for (final c in widget.chars) {
      final cid = (c['id'] as num?)?.toInt();
      if (cid != null) byId[cid] = c;
    }
    for (final m in _msgHits) {
      final cid = (m['cid'] as num?)?.toInt() ?? 0;
      final c = byId[cid];
      tiles.add(ListTile(
        leading: DefaultAvatar(url: avatarFull(c?['avatar'] as String?), size: 44),
        title: Text('${c?['name'] ?? ''}', maxLines: 1),
        subtitle: Text(cleanMetaTime('${m['content'] ?? ''}'),
            maxLines: 2, style: const TextStyle(fontSize: 12, color: Colors.grey)),
        trailing: Text(fmtTime(((m['ts'] ?? 0) as num).toDouble()),
            style: const TextStyle(fontSize: 11, color: Color(0xFFB2B2B2))),
        onTap: () => _open(cid),
      ));
    }
    return tiles;
  }

  @override
  Widget build(BuildContext context) {
    final q = _qText;
    final hits = q.isEmpty ? <Widget>[] : _buildHits(q);
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 0,
        title: Container(
          height: 36,
          margin: const EdgeInsets.only(right: 8),
          decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(6)),
          child: TextField(
            controller: _q,
            autofocus: true,
            onChanged: (v) {
              _deb?.cancel();
              _deb = Timer(const Duration(milliseconds: 300), () {
                if (!mounted) return;
                final q = v.trim().toLowerCase();
                setState(() => _qText = q);
                _runSearch(q);
              });
            },
            style: const TextStyle(fontSize: 14),
            decoration: const InputDecoration(
              hintText: '搜索',
              hintStyle: TextStyle(color: Colors.grey, fontSize: 14),
              prefixIcon: Icon(Icons.search, size: 18, color: Colors.grey),
              border: InputBorder.none,
              isDense: true,
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消', style: TextStyle(color: kWeChatTheme)),
          ),
        ],
      ),
      body: hits.isEmpty
          ? Center(
              child: Text(q.isEmpty ? '搜索联系人或聊天记录' : '无搜索结果',
                  style: const TextStyle(color: Colors.grey)))
          : ListView.separated(
              itemCount: hits.length,
              separatorBuilder: (_, _) => const Divider(indent: 68, height: 1),
              itemBuilder: (ctx, i) => hits[i],
            ),
    );
  }
}
