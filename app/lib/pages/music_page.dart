import 'dart:async';
import 'dart:io';
import 'dart:math' as math;

import 'package:audioplayers/audioplayers.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../api.dart';
import '../log.dart';
import '../util.dart';

/// 听一听（音乐）：本地上传 / 列表播放 / 上下一首 / 进度拖动 / 随机
class MusicPage extends StatefulWidget {
  const MusicPage({super.key});

  @override
  State<MusicPage> createState() => _MusicPageState();
}

class _MusicPageState extends State<MusicPage> {
  final AudioPlayer _player = AudioPlayer();
  List<Map<String, dynamic>> _tracks = [];
  bool _loading = true;
  bool _uploading = false;
  bool _shuffle = false;
  int _current = -1;
  bool _playing = false;
  Duration _pos = Duration.zero;
  Duration _dur = Duration.zero;
  bool _dragging = false; // 拖动进度条时暂停跟随播放位置，避免回弹
  StreamSubscription<Duration>? _subPos;
  StreamSubscription<Duration>? _subDur;
  StreamSubscription<void>? _subDone;

  @override
  void initState() {
    super.initState();
    _subPos = _player.onPositionChanged.listen((p) {
      if (mounted && !_dragging) setState(() => _pos = p);
    });
    _subDur = _player.onDurationChanged.listen((d) {
      if (mounted) setState(() => _dur = d);
    });
    _subDone = _player.onPlayerComplete.listen((_) => _next(auto: true));
    _load();
  }

  @override
  void dispose() {
    _subPos?.cancel();
    _subDur?.cancel();
    _subDone?.cancel();
    _player.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final d = await Api.music();
      final list =
          ((d['music'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
      if (mounted) {
        setState(() {
          _tracks = list;
          _loading = false;
        });
      }
    } catch (e) {
      Log.error('music', '加载音乐失败: $e');
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _upload() async {
    final res = await FilePicker.platform.pickFiles(type: FileType.audio, allowMultiple: true);
    if (res == null) return;
    final files = <File>[];
    for (final f in res.files) {
      if (f.path != null) files.add(File(f.path!));
    }
    if (files.isEmpty) return;
    if (!mounted) return;
    setState(() => _uploading = true);
    try {
      final r = await Api.musicUpload(files);
      final n = (r['count'] as num?)?.toInt() ?? 0;
      if (mounted) showToast(context, '已上传 $n 首');
      await _load();
    } catch (_) {
      if (mounted) showToast(context, '上传失败');
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  Future<void> _playIndex(int i) async {
    if (i < 0 || i >= _tracks.length) return;
    final m = _tracks[i];
    try {
      await _player.stop();
      await _player.play(UrlSource(fullUrl('${m['url']}')));
      if (mounted) {
        setState(() {
          _current = i;
          _playing = true;
          _pos = Duration.zero;
          _dur = Duration.zero;
        });
      }
    } catch (e) {
      Log.error('music', '播放失败: $e');
      if (mounted) showToast(context, '播放失败');
    }
  }

  Future<void> _toggle() async {
    if (_current < 0) {
      if (_tracks.isNotEmpty) await _playIndex(0);
      return;
    }
    if (_playing) {
      await _player.pause();
      if (mounted) setState(() => _playing = false);
    } else {
      await _player.resume();
      if (mounted) setState(() => _playing = true);
    }
  }

  void _next({bool auto = false}) {
    if (_tracks.isEmpty) return;
    if (_current < 0) {
      _playIndex(0);
      return;
    }
    final int n;
    if (_shuffle) {
      n = _tracks.length == 1 ? 0 : (() {
        var x = math.Random().nextInt(_tracks.length);
        if (x == _current) x = (x + 1) % _tracks.length;
        return x;
      })();
    } else {
      n = _current + 1 >= _tracks.length ? 0 : _current + 1;
    }
    _playIndex(n);
  }

  void _prev() {
    if (_tracks.isEmpty || _current < 0) return;
    final n = _current - 1 < 0 ? _tracks.length - 1 : _current - 1;
    _playIndex(n);
  }

  Future<void> _delete(Map<String, dynamic> m) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除'),
        content: Text('确定删除「${m['title']}」吗？'),
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
      final deletedId = (m['id'] as num).toInt();
      final delIdx = _tracks.indexWhere((e) => e['id'] == deletedId);
      final wasCurrent = _current >= 0 && _tracks[_current]['id'] == deletedId;
      final beforeCurrent = !wasCurrent && delIdx >= 0 && delIdx < _current;
      if (wasCurrent) {
        await _player.stop();
        if (mounted) {
          setState(() {
            _current = -1;
            _playing = false;
          });
        }
      }
      await Api.musicDelete(deletedId);
      await _load();
      if (mounted && beforeCurrent && _current > 0) {
        setState(() => _current = _current - 1); // 删除的曲目在当前曲目之前，修正索引
      }
    } catch (_) {
      if (mounted) showToast(context, '删除失败');
    }
  }

  String _fmtDur(Duration d) {
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$m:$s';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('听一听'),
        actions: [
          if (_uploading)
            const Padding(
              padding: EdgeInsets.only(right: 14),
              child: Center(
                  child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))),
            )
          else
            IconButton(icon: const Icon(Icons.add), tooltip: '上传音乐', onPressed: _upload),
          const SizedBox(width: 4),
        ],
      ),
      body: Column(children: [
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator())
              : _tracks.isEmpty
                  ? const Center(
                      child: Text('还没有音乐\n点右上角 ＋ 上传歌曲',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: Colors.grey, height: 1.6)))
                  : RefreshIndicator(
                      color: kWeChatTheme,
                      onRefresh: _load,
                      child: ListView.separated(
                        physics: const AlwaysScrollableScrollPhysics(),
                        itemCount: _tracks.length,
                        separatorBuilder: (_, _) => const Divider(indent: 60),
                        itemBuilder: (ctx, i) {
                          final m = _tracks[i];
                          final cur = i == _current;
                          return InkWell(
                            onTap: () => cur ? _toggle() : _playIndex(i),
                            onLongPress: () => _delete(m),
                            child: Container(
                              color: Colors.white,
                              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                              child: Row(children: [
                                Icon(
                                  cur && _playing
                                      ? Icons.graphic_eq
                                      : (cur ? Icons.pause_circle_outline : Icons.music_note),
                                  size: 22,
                                  color: cur ? kWeChatTheme : const Color(0xFF888888),
                                ),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Column(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        Text('${m['title'] ?? ''}',
                                            style: TextStyle(
                                                fontSize: 15.5,
                                                color: cur ? kWeChatTheme : Colors.black),
                                            maxLines: 1,
                                            overflow: TextOverflow.ellipsis),
                                        const SizedBox(height: 3),
                                        Text(fmtBytes((m['size'] as num?)?.toInt()),
                                            style: const TextStyle(
                                                fontSize: 12, color: Color(0xFF999999))),
                                      ]),
                                ),
                                if (cur)
                                  Text(_fmtDur(_dur),
                                      style: const TextStyle(
                                          fontSize: 12, color: Color(0xFF999999))),
                              ]),
                            ),
                          );
                        },
                      ),
                    ),
        ),
        if (_tracks.isNotEmpty && _current >= 0) _playerBar(),
      ]),
    );
  }

  Widget _playerBar() {
    final title = '${_tracks[_current]['title'] ?? ''}';
    final total = _dur.inMilliseconds <= 0 ? 1 : _dur.inMilliseconds;
    final v = _pos.inMilliseconds.clamp(0, total).toDouble();
    return Container(
      color: Colors.white,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 8, 14, 6),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Row(children: [
              Expanded(
                child: Text(title,
                    style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis),
              ),
              Text('${_fmtDur(_pos)} / ${_fmtDur(_dur)}',
                  style: const TextStyle(fontSize: 12, color: Color(0xFF999999))),
            ]),
            Slider(
              value: v,
              max: total.toDouble(),
              onChanged: (x) {
                _dragging = true;
                setState(() => _pos = Duration(milliseconds: x.toInt()));
              },
              onChangeEnd: (x) async {
                try {
                  await _player.seek(Duration(milliseconds: x.toInt()));
                } finally {
                  _dragging = false;
                }
              },
            ),
            Row(mainAxisAlignment: MainAxisAlignment.center, children: [
              IconButton(
                  icon: Icon(_shuffle ? Icons.shuffle : Icons.shuffle_outlined,
                      size: 22, color: _shuffle ? kWeChatTheme : const Color(0xFF666666)),
                  onPressed: () => setState(() => _shuffle = !_shuffle)),
              IconButton(
                  icon: const Icon(Icons.skip_previous, size: 32, color: Color(0xFF333333)),
                  onPressed: _prev),
              IconButton(
                icon: Icon(_playing ? Icons.pause_circle_filled : Icons.play_circle_filled,
                    size: 46, color: kWeChatTheme),
                onPressed: _toggle,
              ),
              IconButton(
                  icon: const Icon(Icons.skip_next, size: 32, color: Color(0xFF333333)),
                  onPressed: () => _next()),
              const SizedBox(width: 48),
            ]),
          ]),
        ),
      ),
    );
  }
}
