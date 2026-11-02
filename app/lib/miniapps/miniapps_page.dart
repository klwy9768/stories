import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api.dart';
import '../log.dart';
import '../util.dart';
import 'miniapp_web_page.dart';

/// 小程序中心（微信风格：搜索 / 最近使用 / 我的小程序 / 管理）
class MiniAppsPage extends StatefulWidget {
  const MiniAppsPage({super.key});

  @override
  State<MiniAppsPage> createState() => _MiniAppsPageState();
}

class _MiniAppsPageState extends State<MiniAppsPage> {
  final _q = TextEditingController();
  List<Map<String, dynamic>> _apps = [];
  List<String> _recent = [];
  List<String> _mine = [];
  bool _loading = true;
  bool _managing = false;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _q.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      _recent = prefs.getStringList('miniapps_recent') ?? [];
      final mine = prefs.getStringList('miniapps_mine');
      final d = await Api.miniapps();
      final apps = ((d['apps'] as List?) ?? [])
          .map((e) => (e as Map).cast<String, dynamic>())
          .where((e) => '${e['id'] ?? ''}'.isNotEmpty)
          .toList();
      if (!mounted) return;
      setState(() {
        _apps = apps;
        _mine = mine ?? apps.map((e) => '${e['id']}').toList(); // 首次默认全部
        _loading = false;
        _failed = false;
      });
      if (mine == null) _saveMine();
    } catch (e) {
      Log.error('miniapps', '加载失败: $e');
      if (mounted) {
        setState(() {
          _loading = false;
          _failed = true;
        });
      }
    }
  }

  Map<String, dynamic>? _byId(String id) {
    for (final a in _apps) {
      if ('${a['id']}' == id) return a;
    }
    return null;
  }

  Future<void> _saveMine() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList('miniapps_mine', _mine);
    } catch (_) {}
  }

  Future<void> _saveRecent() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList('miniapps_recent', _recent);
    } catch (_) {}
  }

  void _open(Map<String, dynamic> app) {
    final id = '${app['id']}';
    _recent.remove(id);
    _recent.insert(0, id);
    if (_recent.length > 8) _recent = _recent.sublist(0, 8);
    _saveRecent();
    setState(() {});
    Navigator.push(context, MaterialPageRoute(builder: (_) => MiniAppWebPage(app: app)));
  }

  void _toggleMine(String id) {
    setState(() {
      if (_mine.contains(id)) {
        _mine.remove(id);
      } else {
        _mine.add(id);
      }
    });
    _saveMine();
  }

  Color _colorOf(Map<String, dynamic> a) {
    try {
      final s = '${a['color'] ?? ''}'.replaceFirst('#', '');
      if (s.length == 6) return Color(int.parse('FF$s', radix: 16));
    } catch (_) {}
    return const Color(0xFF07C160);
  }

  Widget _grid(List<Map<String, dynamic>> apps, {required bool managing}) {
    return GridView.count(
      crossAxisCount: 4,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
      mainAxisSpacing: 4,
      crossAxisSpacing: 4,
      childAspectRatio: 0.82,
      children: [
        for (final a in apps)
          GestureDetector(
            onTap: managing ? () => _toggleMine('${a['id']}') : () => _open(a),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Stack(children: [
                Container(
                  width: 56,
                  height: 56,
                  decoration: BoxDecoration(
                    color: _colorOf(a),
                    borderRadius: BorderRadius.circular(14),
                  ),
                  alignment: Alignment.center,
                  child: Text('${a['icon'] ?? '📦'}', style: const TextStyle(fontSize: 28)),
                ),
                if (managing)
                  Positioned(
                    right: -4,
                    top: -4,
                    child: Container(
                      width: 20,
                      height: 20,
                      decoration: BoxDecoration(
                        color: _mine.contains('${a['id']}')
                            ? const Color(0xFF07C160)
                            : Colors.black38,
                        shape: BoxShape.circle,
                        border: Border.all(color: Colors.white, width: 1.5),
                      ),
                      child: Icon(_mine.contains('${a['id']}') ? Icons.check : Icons.add,
                          size: 13, color: Colors.white),
                    ),
                  ),
              ]),
              const SizedBox(height: 5),
              Text('${a['name'] ?? ''}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 12)),
            ]),
          ),
      ],
    );
  }

  Widget _section(String title, List<Map<String, dynamic>> apps, {bool managing = false}) {
    return Container(
      margin: const EdgeInsets.only(top: 10),
      color: Colors.white,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 2),
          child: Text(title, style: const TextStyle(fontSize: 13, color: Color(0xFF999999))),
        ),
        if (apps.isEmpty)
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 4, 16, 14),
            child: Text('还没有添加，去「全部小程序」点管理添加',
                style: TextStyle(fontSize: 13, color: Colors.grey)),
          )
        else
          _grid(apps, managing: managing),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final q = _q.text.trim().toLowerCase();
    final mineApps = [for (final id in _mine) if (_byId(id) != null) _byId(id)!];
    final recentApps = [for (final id in _recent) if (_byId(id) != null) _byId(id)!];
    final filtered = q.isEmpty
        ? _apps
        : _apps
            .where((a) =>
                '${a['name']}'.toLowerCase().contains(q) ||
                '${a['desc'] ?? ''}'.toLowerCase().contains(q))
            .toList();
    return Scaffold(
      appBar: AppBar(
        title: const Text('小程序'),
        actions: [
          TextButton(
            onPressed: _apps.isEmpty ? null : () => setState(() => _managing = !_managing),
            child: Text(_managing ? '完成' : '管理',
                style: const TextStyle(color: kWeChatTheme, fontSize: 15)),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : (_failed && _apps.isEmpty)
              ? Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    const Text('小程序加载失败，请检查网络', style: TextStyle(color: Colors.grey)),
                    const SizedBox(height: 12),
                    FilledButton(
                      onPressed: () {
                        setState(() {
                          _loading = true;
                          _failed = false;
                        });
                        _load();
                      },
                      child: const Text('重试'),
                    ),
                  ]),
                )
              : RefreshIndicator(
              color: kWeChatTheme,
              onRefresh: _load,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                children: [
                  Container(
                    color: Colors.white,
                    padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
                    child: Container(
                      height: 36,
                      padding: const EdgeInsets.symmetric(horizontal: 10),
                      decoration: BoxDecoration(
                          color: const Color(0xFFF2F2F2), borderRadius: BorderRadius.circular(8)),
                      child: Row(children: [
                        const Icon(Icons.search, size: 18, color: Color(0xFF999999)),
                        const SizedBox(width: 6),
                        Expanded(
                          child: TextField(
                            controller: _q,
                            onChanged: (_) => setState(() {}),
                            style: const TextStyle(fontSize: 14),
                            decoration: const InputDecoration(
                              hintText: '搜索小程序',
                              hintStyle: TextStyle(color: Colors.grey, fontSize: 14),
                              border: InputBorder.none,
                              isDense: true,
                            ),
                          ),
                        ),
                      ]),
                    ),
                  ),
                  if (q.isNotEmpty)
                    _section('搜索结果', filtered, managing: _managing)
                  else if (_managing)
                    _section('点按图标 添加 / 移除', _apps, managing: true)
                  else ...[
                    if (recentApps.isNotEmpty) _section('最近使用', recentApps),
                    _section('我的小程序', mineApps),
                    _section('全部小程序', _apps),
                  ],
                  const SizedBox(height: 20),
                ],
              ),
            ),
    );
  }
}
