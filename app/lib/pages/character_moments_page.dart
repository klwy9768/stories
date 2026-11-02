import 'package:flutter/material.dart';

import '../api.dart';
import '../log.dart';
import '../util.dart';
import 'moments_page.dart' show isMomentVideo;

/// 某个角色的朋友圈相册（点头像/昵称进入，看他/她自己的所有动态）
class CharacterMomentsPage extends StatefulWidget {
  final int cid;
  final String name;
  final String? avatar;
  const CharacterMomentsPage({super.key, required this.cid, required this.name, this.avatar});

  @override
  State<CharacterMomentsPage> createState() => _CharacterMomentsPageState();
}

class _CharacterMomentsPageState extends State<CharacterMomentsPage> {
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await Api.moments();
      final list = ((d['moments'] as List?) ?? [])
          .map((e) => (e as Map).cast<String, dynamic>())
          .where((m) =>
              (m['author'] ?? 'character') == 'character' &&
              ((m['character_id'] as num?)?.toInt() ?? 0) == widget.cid)
          .toList();
      if (mounted) {
        setState(() {
          _items = list;
          _loading = false;
        });
      }
    } catch (e) {
      Log.error('moments', '加载角色朋友圈失败: $e');
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.name.isEmpty ? '朋友圈' : widget.name)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              color: kWeChatTheme,
              onRefresh: _load,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: EdgeInsets.zero,
                children: [
                  Container(
                    color: Colors.white,
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
                    child: Row(children: [
                      DefaultAvatar(url: fullUrl(widget.avatar), size: 56),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Text(widget.name,
                              style:
                                  const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
                          const SizedBox(height: 4),
                          Text('共 ${_items.length} 条动态',
                              style: const TextStyle(fontSize: 13, color: Color(0xFF999999))),
                        ]),
                      ),
                    ]),
                  ),
                  if (_items.isEmpty)
                    const Padding(
                        padding: EdgeInsets.all(40),
                        child: Center(
                            child: Text('还没有动态', style: TextStyle(color: Colors.grey))))
                  else
                    for (final m in _items.reversed) _momentCard(m),
                ],
              ),
            ),
    );
  }

  Widget _momentCard(Map<String, dynamic> m) {
    final images = ((m['images'] as List?) ?? []).cast<dynamic>().map((e) => '$e').toList();
    final comments = ((m['comments'] as List?) ?? [])
        .map((e) => (e as Map).cast<String, dynamic>())
        .toList();
    final likeNames = ((m['like_names'] as List?) ?? []).map((e) => '$e').toList();
    final content = '${m['content'] ?? ''}'.trim();
    final single = images.length == 1;
    final sz = single ? 200.0 : 104.0;
    return Container(
      color: Colors.white,
      margin: const EdgeInsets.only(top: 10),
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (content.isNotEmpty) Text(cleanMetaTime(content), style: const TextStyle(fontSize: 15.5, height: 1.45)),
        if (images.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Wrap(
              spacing: 4,
              runSpacing: 4,
              children: [
                for (final u in images)
                  isMomentVideo(u)
                      ? GestureDetector(
                          onTap: () => showVideoViewer(context, fullUrl(u)),
                          child: SizedBox(
                            width: sz,
                            height: sz == 200 ? 132 : 104,
                            child: Stack(alignment: Alignment.center, children: [
                              VideoThumb(
                                  url: fullUrl(u),
                                  width: sz,
                                  height: sz == 200 ? 132 : 104,
                                  radius: BorderRadius.circular(4)),
                              const Icon(Icons.play_circle_outline,
                                  color: Colors.white70, size: 30),
                            ]),
                          ),
                        )
                      : GestureDetector(
                          onTap: () => showImageViewer(context, fullUrl(u)),
                          child: ClipRRect(
                            borderRadius: BorderRadius.circular(4),
                            child: Image.network(
                              fullUrl(u),
                              width: sz,
                              height: sz,
                              fit: BoxFit.cover,
                              cacheWidth: 300,
                              errorBuilder: (_, _, _) => Container(
                                  width: sz, height: sz, color: Colors.black12),
                            ),
                          ),
                        ),
              ],
            ),
          ),
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text(fmtTime((m['ts'] as num?)?.toDouble()),
              style: const TextStyle(fontSize: 12.5, color: Color(0xFF999999))),
        ),
        if (likeNames.isNotEmpty || comments.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
              decoration: BoxDecoration(
                  color: const Color(0xFFF7F7F7), borderRadius: BorderRadius.circular(4)),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                if (likeNames.isNotEmpty)
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Padding(
                        padding: EdgeInsets.only(top: 2),
                        child: Icon(Icons.favorite_border, size: 14, color: Color(0xFF576B95))),
                    const SizedBox(width: 5),
                    Expanded(
                        child: Text(likeNames.join('、'),
                            style: const TextStyle(
                                fontSize: 13.5, color: Color(0xFF576B95), height: 1.4))),
                  ]),
                for (final c in comments)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 2),
                    child: Text.rich(TextSpan(children: [
                      TextSpan(
                          text: '${c['name'] ?? widget.name}：',
                          style: const TextStyle(color: Color(0xFF576B95), fontSize: 13.5)),
                      TextSpan(
                          text: cleanMetaTime('${c['content'] ?? ''}'),
                          style: const TextStyle(fontSize: 13.5, height: 1.4)),
                    ])),
                  ),
              ]),
            ),
          ),
      ]),
    );
  }
}
