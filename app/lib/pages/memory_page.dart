import 'package:flutter/material.dart';

import '../api.dart';
import '../util.dart';

/// 她的回忆：查看 / 整理角色的长期记忆（自动从聊天记录入库的「可检索记忆」）
///
/// 说明：记忆是按角色隔离的私聊记忆；这里改内容会触发服务端重新嵌入，
/// 置顶的记忆不会被"遗忘曲线"淡出。
class MemoryPage extends StatefulWidget {
  final int cid;
  final String name;
  const MemoryPage({super.key, required this.cid, required this.name});

  @override
  State<MemoryPage> createState() => _MemoryPageState();
}

class _MemoryPageState extends State<MemoryPage> {
  final _q = TextEditingController();
  final _scroll = ScrollController();
  List<Map<String, dynamic>> _items = [];
  int _total = 0;
  bool _loading = true;
  bool _more = false;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(() {
      if (_scroll.position.pixels >= _scroll.position.maxScrollExtent - 200) {
        _loadMore();
      }
    });
    _load(0);
  }

  @override
  void dispose() {
    _q.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load(int offset) async {
    setState(() {
      _loading = offset == 0;
      _more = offset > 0;
    });
    try {
      final r = await Api.memoryList(widget.cid,
          q: _q.text.trim(), offset: offset, limit: 50);
      final list = (r['items'] as List?) ?? [];
      final parsed = list.map((e) => (e as Map).cast<String, dynamic>()).toList();
      if (!mounted) return;
      setState(() {
        _total = (r['total'] as num?)?.toInt() ?? 0;
        if (offset == 0) {
          _items = parsed;
        } else {
          _items = [..._items, ...parsed];
        }
      });
    } catch (e) {
      if (mounted) showToast(context, '加载失败，请重试');
    } finally {
      if (mounted) {
        setState(() {
          _loading = false;
          _more = false;
        });
      }
    }
  }

  void _loadMore() {
    if (_loading || _more || _items.length >= _total) return;
    _load(_items.length);
  }

  Future<void> _update(Map<String, dynamic> it,
      {int? pin, int? dormant, String? content}) async {
    try {
      await Api.memoryUpdate(widget.cid, (it['mid'] as num).toInt(),
          pin: pin, dormant: dormant, content: content);
      await _load(0);
    } catch (e) {
      if (mounted) showToast(context, '操作失败');
    }
  }

  Future<void> _delete(Map<String, dynamic> it) async {
    try {
      await Api.memoryDelete(widget.cid, (it['mid'] as num).toInt());
      if (!mounted) return;
      setState(() {
        _items.removeWhere((x) => x['mid'] == it['mid']);
        if (_total > 0) _total -= 1;
      });
      showToast(context, '已删除');
    } catch (e) {
      if (mounted) showToast(context, '删除失败');
    }
  }

  Future<void> _edit(Map<String, dynamic> it) async {
    final ctl = TextEditingController(text: '${it['content'] ?? ''}');
    final v = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('纠正记忆内容'),
        content: TextField(
          controller: ctl,
          maxLines: 5,
          decoration: const InputDecoration(hintText: '她应该记住的版本'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, ctl.text.trim()), child: const Text('保存')),
        ],
      ),
    );
    if (v == null || v.isEmpty) return;
    await _update(it, content: v);
    if (mounted) showToast(context, '已保存（会重新学习这句话）');
  }

  void _menu(Map<String, dynamic> it) {
    final pinned = ((it['pin'] as num?)?.toInt() ?? 0) > 0;
    final dormant = ((it['dormant'] as num?)?.toInt() ?? 0) > 0;
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: Icon(pinned ? Icons.push_pin_outlined : Icons.push_pin),
            title: Text(pinned ? '取消置顶' : '置顶（不会被忘记）'),
            onTap: () {
              Navigator.pop(ctx);
              _update(it, pin: pinned ? 0 : 1);
            },
          ),
          if (dormant)
            ListTile(
              leading: const Icon(Icons.restore),
              title: const Text('恢复（重新参与回忆）'),
              onTap: () {
                Navigator.pop(ctx);
                _update(it, dormant: 0);
              },
            ),
          ListTile(
            leading: const Icon(Icons.edit_outlined),
            title: const Text('纠正内容'),
            onTap: () {
              Navigator.pop(ctx);
              _edit(it);
            },
          ),
          ListTile(
            leading: const Icon(Icons.delete_outline, color: Colors.red),
            title: const Text('删除这条记忆', style: TextStyle(color: Colors.red)),
            onTap: () {
              Navigator.pop(ctx);
              _delete(it);
            },
          ),
        ]),
      ),
    );
  }

  String _timeText(dynamic ts) {
    try {
      final t = (ts as num).toDouble();
      if (t <= 0) return '';
      final d = DateTime.fromMillisecondsSinceEpoch((t * 1000).round());
      return '${d.month}月${d.day}日';
    } catch (_) {
      return '';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('她的回忆 · ${widget.name}'),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(46),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
            child: TextField(
              controller: _q,
              textInputAction: TextInputAction.search,
              onSubmitted: (_) => _load(0),
              decoration: InputDecoration(
                isDense: true,
                hintText: '搜索她记住的事',
                hintStyle: const TextStyle(fontSize: 13, color: Colors.grey),
                prefixIcon: const Icon(Icons.search, size: 18),
                suffixIcon: IconButton(
                  icon: const Icon(Icons.close, size: 16),
                  onPressed: () {
                    _q.clear();
                    _load(0);
                  },
                ),
                filled: true,
                fillColor: const Color(0xFFF2F2F2),
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(8), borderSide: BorderSide.none),
              ),
            ),
          ),
        ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Column(children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(14, 8, 14, 4),
                child: Row(children: [
                  Text('共 $_total 条', style: const TextStyle(fontSize: 12, color: Colors.grey)),
                  const Spacer(),
                  const Text('长按或点右侧菜单可整理',
                      style: TextStyle(fontSize: 11, color: Color(0xFFB2B2B2))),
                ]),
              ),
              Expanded(
                child: _items.isEmpty
                    ? Center(
                        child: Text(_q.text.isEmpty ? '她还没有记住什么' : '没有匹配的记忆',
                            style: const TextStyle(color: Colors.grey)))
                    : ListView.separated(
                        controller: _scroll,
                        itemCount: _items.length + (_more ? 1 : 0),
                        separatorBuilder: (_, _) => const Divider(height: 1, indent: 14),
                        itemBuilder: (ctx, i) {
                          if (i >= _items.length) {
                            return const Padding(
                              padding: EdgeInsets.all(14),
                              child: Center(child: SizedBox(
                                  width: 18, height: 18,
                                  child: CircularProgressIndicator(strokeWidth: 2))),
                            );
                          }
                          return _tile(_items[i]);
                        },
                      ),
              ),
            ]),
    );
  }

  Widget _tile(Map<String, dynamic> it) {
    final pinned = ((it['pin'] as num?)?.toInt() ?? 0) > 0;
    final dormant = ((it['dormant'] as num?)?.toInt() ?? 0) > 0;
    final recallN = (it['recall_n'] as num?)?.toInt() ?? 0;
    final isUser = '${it['role'] ?? ''}' == 'user';
    return ListTile(
      title: Text('${it['content'] ?? ''}', style: const TextStyle(fontSize: 14.5)),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(children: [
          Text(_timeText(it['ts']), style: const TextStyle(fontSize: 11.5, color: Colors.grey)),
          const SizedBox(width: 8),
          Text(isUser ? '对方说的' : '她说的', style: const TextStyle(fontSize: 11.5, color: Colors.grey)),
          if (recallN > 0) ...[
            const SizedBox(width: 8),
            Text('想起过 $recallN 次', style: const TextStyle(fontSize: 11.5, color: Color(0xFF07C160))),
          ],
          if (pinned) ...[
            const SizedBox(width: 8),
            const Icon(Icons.push_pin, size: 12, color: Color(0xFF07C160)),
          ],
          if (dormant) ...[
            const SizedBox(width: 8),
            const Text('已淡出', style: TextStyle(fontSize: 11.5, color: Color(0xFFB2B2B2))),
          ],
        ]),
      ),
      trailing: IconButton(
        icon: const Icon(Icons.more_horiz, size: 20, color: Color(0xFF999999)),
        onPressed: () => _menu(it),
      ),
      onLongPress: () => _menu(it),
    );
  }
}
