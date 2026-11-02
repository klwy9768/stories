import 'package:flutter/material.dart';

import '../api.dart';
import '../log.dart';
import '../util.dart';

/// 世界（跨角色共享动态）：查看角色间发生的大事时间线
class WorldsPage extends StatefulWidget {
  const WorldsPage({super.key});

  @override
  State<WorldsPage> createState() => _WorldsPageState();
}

class _WorldsPageState extends State<WorldsPage> {
  List<Map<String, dynamic>> _worlds = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await Api.worlds();
      final list = ((d['worlds'] as List?) ?? [])
          .map((e) => (e as Map).cast<String, dynamic>())
          .toList();
      if (!mounted) return;
      setState(() {
        _worlds = list;
        _loading = false;
      });
    } catch (e) {
      Log.error('worlds', '加载失败: $e');
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('世界')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _worlds.isEmpty
              ? const Center(
                  child: Text('还没有世界\n可在电脑后台创建',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: Colors.grey, height: 1.6)))
              : RefreshIndicator(
                  color: kWeChatTheme,
                  onRefresh: _load,
                  child: ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.only(bottom: 16),
                    children: [for (final w in _worlds) _worldCard(w)],
                  ),
                ),
    );
  }

  Widget _worldCard(Map<String, dynamic> w) {
    final events = ((w['events'] as List?) ?? [])
        .map((e) => (e as Map).cast<String, dynamic>())
        .toList();
    return Container(
      margin: const EdgeInsets.fromLTRB(10, 10, 10, 0),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          const Icon(Icons.public, size: 18, color: Color(0xFF07C160)),
          const SizedBox(width: 6),
          Text('世界「${w['name'] ?? ''}」',
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
        ]),
        const SizedBox(height: 8),
        if (events.isEmpty)
          const Text('暂无动态', style: TextStyle(fontSize: 13, color: Color(0xFF999999)))
        else
          for (final e in events.reversed) _eventRow(e),
      ]),
    );
  }

  Widget _eventRow(Map<String, dynamic> e) {
    final sender = '${e['sender_name'] ?? e['sender'] ?? ''}';
    final sname = sender.isEmpty || sender == 'system' ? '系统' : sender;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(
          margin: const EdgeInsets.only(top: 6),
          width: 6,
          height: 6,
          decoration: const BoxDecoration(color: Color(0xFF07C160), shape: BoxShape.circle),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(cleanMetaTime('${e['content'] ?? ''}'), style: const TextStyle(fontSize: 14.5, height: 1.4)),
            const SizedBox(height: 2),
            Text('$sname · ${fmtTime(((e['ts'] as num?) ?? 0).toDouble())}',
                style: const TextStyle(fontSize: 11, color: Color(0xFFB2B2B2))),
          ]),
        ),
      ]),
    );
  }
}
