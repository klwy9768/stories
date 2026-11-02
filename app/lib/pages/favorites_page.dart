import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';
import 'package:video_player/video_player.dart';

import '../api.dart';
import '../log.dart';
import '../pick.dart';
import '../util.dart';

/// 收藏（网盘）：上传 / 预览 / 打开 / 重命名 / 删除
class FavoritesPage extends StatefulWidget {
  const FavoritesPage({super.key});

  @override
  State<FavoritesPage> createState() => _FavoritesPageState();
}

class _FavoritesPageState extends State<FavoritesPage> {
  final AudioPlayer _audio = AudioPlayer();
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;
  bool _uploading = false;
  int _playingId = -1;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _audio.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final d = await Api.favorites();
      final list =
          ((d['favorites'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
      if (mounted) {
        setState(() {
          _items = list;
          _loading = false;
        });
      }
    } catch (e) {
      Log.error('fav', '加载收藏失败: $e');
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _pickUpload() async {
    final choice = await showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: const Icon(Icons.photo_library_outlined),
            title: const Text('照片 / 视频'),
            onTap: () => Navigator.pop(ctx, 'media'),
          ),
          ListTile(
            leading: const Icon(Icons.attach_file),
            title: const Text('文件'),
            onTap: () => Navigator.pop(ctx, 'file'),
          ),
          const SizedBox(height: 6),
        ]),
      ),
    );
    if (choice == null) return;
    if (!mounted) return;
    final files = <File>[];
    if (choice == 'media') {
      final fs = await pickAssets(context, max: 9, type: RequestType.common);
      for (final f in fs) {
        files.add(isVideoPath(f.path) ? f : await ensureJpeg(f));
      }
    } else {
      await ensureAllFilesAccess(context);
      if (!mounted) return;
      final res = await FilePicker.platform.pickFiles(allowMultiple: true);
      if (res != null) {
        for (final f in res.files) {
          if (f.path != null) files.add(File(f.path!));
        }
      }
    }
    if (files.isEmpty) return;
    if (!mounted) return;
    setState(() => _uploading = true);
    try {
      final r = await Api.favoriteUpload(files);
      final n = (r['count'] as num?)?.toInt() ?? 0;
      if (mounted) showToast(context, '已上传 $n 个文件');
      await _load();
    } catch (_) {
      if (mounted) showToast(context, '上传失败');
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  Future<void> _rename(Map<String, dynamic> m) async {
    final ctrl = TextEditingController(text: '${m['name'] ?? ''}');
    final v = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('重命名'),
        content: TextField(controller: ctrl, autofocus: true, maxLength: 120),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('取消', style: TextStyle(color: Colors.black))),
          TextButton(onPressed: () => Navigator.pop(ctx, ctrl.text), child: const Text('保存')),
        ],
      ),
    );
    ctrl.dispose();
    if (v == null || v.trim().isEmpty) return;
    try {
      await Api.favoriteRename((m['id'] as num).toInt(), v.trim());
      await _load();
    } catch (_) {
      if (mounted) showToast(context, '重命名失败');
    }
  }

  Future<void> _delete(Map<String, dynamic> m) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除'),
        content: Text('确定删除「${m['name']}」吗？'),
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
      await Api.favoriteDelete((m['id'] as num).toInt());
      await _load();
    } catch (_) {
      if (mounted) showToast(context, '删除失败');
    }
  }

  Future<void> _openFile(Map<String, dynamic> m) async {
    final client = http.Client();
    try {
      if (mounted) showToast(context, '正在下载…');
      final req = http.Request('GET', Uri.parse(fullUrl('${m['url']}')));
      final resp = await client.send(req);
      if (resp.statusCode != 200) throw Exception('HTTP ${resp.statusCode}');
      final dir = await getTemporaryDirectory();
      final safe = 'fav_${m['id']}_${m['name']}'.replaceAll(RegExp(r'[\\/:*?"<>|]'), '_');
      final f = File('${dir.path}/$safe');
      // 流式写盘：大文件不再整块读进内存，避免 OOM
      final sink = f.openWrite();
      try {
        await resp.stream.pipe(sink);
      } catch (_) {
        try {
          await sink.close();
        } catch (_) {}
        rethrow;
      }
      final expectLen = resp.contentLength ?? 0;
      if (expectLen > 0 && await f.length() != expectLen) {
        throw Exception('下载不完整');
      }
      await OpenFilex.open(f.path);
    } catch (e) {
      Log.error('fav', '打开文件失败: $e');
      if (mounted) showToast(context, '打开失败');
    } finally {
      client.close();
    }
  }

  Future<void> _playAudio(Map<String, dynamic> m) async {
    final id = (m['id'] as num).toInt();
    if (_playingId == id) {
      await _audio.stop();
      if (mounted) setState(() => _playingId = -1);
      return;
    }
    try {
      await _audio.stop();
      await _audio.play(UrlSource(fullUrl('${m['url']}')));
      if (mounted) setState(() => _playingId = id);
    } catch (_) {}
  }

  void _onTap(Map<String, dynamic> m) {
    final kind = '${m['kind']}';
    if (kind == 'image') {
      showImageViewer(context, fullUrl('${m['url']}'));
    } else if (kind == 'video') {
      Navigator.push(
          context,
          MaterialPageRoute(
              builder: (_) => _FavVideoPage(url: fullUrl('${m['url']}'), title: '${m['name']}')));
    } else if (kind == 'audio') {
      _playAudio(m);
    } else {
      _openFile(m);
    }
  }

  Widget _lead(Map<String, dynamic> m) {
    final kind = '${m['kind']}';
    if (kind == 'image') {
      return ClipRRect(
        borderRadius: BorderRadius.circular(6),
        child: Image.network(
          fullUrl('${m['url']}'),
          width: 44,
          height: 44,
          fit: BoxFit.cover,
          cacheWidth: 132,
          errorBuilder: (_, _, _) => _iconBox(Icons.image_outlined),
        ),
      );
    }
    if (kind == 'video') return _iconBox(Icons.play_circle_outline);
    if (kind == 'audio') return _iconBox(Icons.music_note);
    return _iconBox(Icons.insert_drive_file_outlined);
  }

  Widget _iconBox(IconData icon) {
    return Container(
      width: 44,
      height: 44,
      decoration: BoxDecoration(color: const Color(0xFFF2F2F2), borderRadius: BorderRadius.circular(6)),
      child: Icon(icon, size: 24, color: const Color(0xFF888888)),
    );
  }

  String _kindLabel(String kind) {
    switch (kind) {
      case 'image':
        return '图片';
      case 'video':
        return '视频';
      case 'audio':
        return '音频';
      default:
        return '文件';
    }
  }

  @override
  Widget build(BuildContext context) {
    final total = _items.fold<int>(0, (a, m) => a + ((m['size'] as num?)?.toInt() ?? 0));
    return Scaffold(
      appBar: AppBar(
        title: const Text('收藏'),
        actions: [
          if (_uploading)
            const Padding(
              padding: EdgeInsets.only(right: 14),
              child: Center(
                  child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))),
            )
          else
            IconButton(icon: const Icon(Icons.add), tooltip: '上传', onPressed: _pickUpload),
          const SizedBox(width: 4),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _items.isEmpty
              ? const Center(
                  child: Text('还没有收藏\n点右上角 ＋ 上传文件',
                      textAlign: TextAlign.center, style: TextStyle(color: Colors.grey, height: 1.6)))
              : RefreshIndicator(
                  color: kWeChatTheme,
                  onRefresh: _load,
                  child: ListView.separated(
                    physics: const AlwaysScrollableScrollPhysics(),
                    itemCount: _items.length + 1,
                    separatorBuilder: (_, _) => const Divider(indent: 68),
                    itemBuilder: (ctx, i) {
                      if (i == 0) {
                        return Container(
                          color: Colors.white,
                          padding: const EdgeInsets.fromLTRB(16, 10, 16, 8),
                          child: Text('共 ${_items.length} 项 · ${fmtBytes(total)}',
                              style: const TextStyle(fontSize: 13, color: Color(0xFF999999))),
                        );
                      }
                      final m = _items[i - 1];
                      final kind = '${m['kind']}';
                      final playing = _playingId == ((m['id'] as num?)?.toInt() ?? -1);
                      return InkWell(
                        onTap: () => _onTap(m),
                        child: Container(
                          color: Colors.white,
                          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                          child: Row(children: [
                            _lead(m),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                Text('${m['name'] ?? ''}',
                                    style: const TextStyle(fontSize: 15.5),
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis),
                                const SizedBox(height: 3),
                                Text(
                                    '${_kindLabel(kind)} · ${fmtBytes((m['size'] as num?)?.toInt())} · ${fmtTime((m['ts'] as num?)?.toDouble())}',
                                    style: const TextStyle(fontSize: 12, color: Color(0xFF999999))),
                              ]),
                            ),
                            if (playing)
                              const Padding(
                                padding: EdgeInsets.only(right: 6),
                                child: Icon(Icons.graphic_eq, size: 18, color: kWeChatTheme),
                              ),
                            PopupMenuButton<String>(
                              icon: const Icon(Icons.more_horiz, size: 20, color: Color(0xFF999999)),
                              onSelected: (v) {
                                if (v == 'rename') {
                                  _rename(m);
                                } else if (v == 'del') {
                                  _delete(m);
                                }
                              },
                              itemBuilder: (_) => const [
                                PopupMenuItem(value: 'rename', child: Text('重命名')),
                                PopupMenuItem(
                                    value: 'del',
                                    child: Text('删除', style: TextStyle(color: Colors.red))),
                              ],
                            ),
                          ]),
                        ),
                      );
                    },
                  ),
                ),
    );
  }
}

class _FavVideoPage extends StatefulWidget {
  final String url;
  final String title;
  const _FavVideoPage({required this.url, required this.title});

  @override
  State<_FavVideoPage> createState() => _FavVideoPageState();
}

class _FavVideoPageState extends State<_FavVideoPage> {
  late final VideoPlayerController _c;
  bool _err = false;

  @override
  void initState() {
    super.initState();
    _c = VideoPlayerController.networkUrl(Uri.parse(widget.url));
    _c.initialize().then((_) {
      if (!mounted) return;
      setState(() {});
      _c.play();
    }).catchError((_) {
      if (mounted) setState(() => _err = true);
    });
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final a = _c.value.aspectRatio;
    final aspect = (a.isFinite && a > 0.1) ? a : 16 / 9;
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(widget.title, style: const TextStyle(fontSize: 16)),
      ),
      body: Center(
        child: _err
            ? Column(mainAxisSize: MainAxisSize.min, children: const [
                Icon(Icons.videocam_off_outlined, color: Colors.white54, size: 42),
                SizedBox(height: 10),
                Text('视频加载失败', style: TextStyle(color: Colors.white70, fontSize: 14)),
              ])
            : _c.value.isInitialized
                ? GestureDetector(
                    onTap: () => setState(() {
                      _c.value.isPlaying ? _c.pause() : _c.play();
                    }),
                    child: AspectRatio(aspectRatio: aspect, child: VideoPlayer(_c)),
                  )
                : const CircularProgressIndicator(color: Colors.white),
      ),
    );
  }
}
