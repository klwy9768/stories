import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:video_player/video_player.dart';

import 'api.dart';
import 'secrets.dart';

const kWeChatLight = Color(0xFFEDEDED);
const kWeChatGreen = Color(0xFF95EC69);
const kWeChatTheme = Color(0xFF07C160);

/// 相对路径补全为完整 URL（/uploads/... → https://host/uploads/...?token=...）
/// 优先使用短时效票据（urlTicket），拿不到时退回长期令牌。
String fullUrl(String? u) {
  if (u == null || u.isEmpty) return '';
  if (u.startsWith('http')) return u;
  final sep = u.contains('?') ? '&' : '?';
  final tok = urlTicket.isNotEmpty ? urlTicket : appToken;
  return '$apiBase$u${sep}token=$tok';
}

/// 时间标注剥离（服务端已做，这里兜底历史缓存）
/// 宽容规则：方括号内容若全部由"时间注解字符"（今天/是/时间/下午/点分…+ HH:MM）组成，则整段剥离；
/// 含其他文字（如"（别忘了 12:30 开会）"）不受影响
final RegExp _metaTimeRe = RegExp(
    r'[\[【（(［〔][今天昨前明后日月年星期周一二三四五六日天号时现在是的时间约大概上午中午下午晚上凌晨早上夜里傍清左右半点分刻钟秒整刚才能0123456789:： 　-]*?\d{1,2}\s*[:：]\s*\d{2}[今天昨前明后日月年星期周一二三四五六日天号时现在是的时间约大概上午中午下午晚上凌晨早上夜里傍清左右半点分刻钟秒整刚才能0123456789:： 　-]*[，。、:：]*[\]】）)］〕]');
final RegExp _metaTimeBrokenRe = RegExp(
    r'^(?:[今天昨前明后日月年星期周一二三四五六日天号时现在是的时间约大概上午中午下午晚上凌晨早上夜里傍清左右半点分刻钟秒整刚才能0123456789:： 　-]{0,16}?\d{1,2}\s*[:：]\s*\d{2}[今天昨前明后日月年星期周一二三四五六日天号时现在是的时间约大概上午中午下午晚上凌晨早上夜里傍清左右半点分刻钟秒整刚才能0123456789:： 　-]{0,16}?[\]】）)］〕])[\s，、。！？:：]*',
    multiLine: true);
final RegExp _orphanCloseRe = RegExp(r'^\s*[\]】）)］〕]\s*', multiLine: true);
final RegExp _orphanOpenRe = RegExp(r'^[\[【（(［〔]\s*$', multiLine: true);
final RegExp _orphanDanglingRe = RegExp(r'^[\[【（(［〔][^\]】）)］〕\n]{0,14}$', multiLine: true);

String cleanMetaTime(String s) {
  if (s.isEmpty) return s;
  try {
    var out = s.replaceAll(_metaTimeRe, '').replaceAll(_metaTimeBrokenRe, '');
    out = out.replaceAll(_orphanCloseRe, '').replaceAll(_orphanOpenRe, '').replaceAll(_orphanDanglingRe, '');
    out = out.replaceAll(RegExp(r'^[\s，、。；：]+'), '');
    return out.trim();
  } catch (_) {
    return s;
  }
}

String fmtTime(num? ts) {
  if (ts == null || ts <= 0) return '';
  final dt = DateTime.fromMillisecondsSinceEpoch((ts * 1000).toInt());
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(dt.year, dt.month, dt.day);
  final diff = today.difference(day).inDays;
  final hm = '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  if (diff <= 0) return hm; // 今天：13:05
  if (diff == 1) return '昨天'; // 昨天：昨天
  if (diff < 7) {
    const wd = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
    return wd[dt.weekday - 1]; // 一周内：周二
  }
  if (dt.year == now.year) return '${dt.month}月${dt.day}日'; // 今年：6月19日
  return '${dt.year}年${dt.month}月${dt.day}日'; // 更早：2025年10月23日
}

/// 微信风格的聊天时间分隔文案（24 小时制）：
/// 今天「13:05」/ 昨天「昨天 13:13」/ 一周内「星期二 13:13」/ 更早「2026年3月5日 13:13」
String wechatChatTime(num? ts) {
  if (ts == null || ts <= 0) return '';
  final dt = DateTime.fromMillisecondsSinceEpoch((ts * 1000).toInt());
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(dt.year, dt.month, dt.day);
  final diff = today.difference(day).inDays;
  final hm = '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  if (diff <= 0) return hm;
  if (diff == 1) return '昨天 $hm';
  if (diff < 7) {
    const wd = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日'];
    return '${wd[dt.weekday - 1]} $hm';
  }
  return '${dt.year}年${dt.month}月${dt.day}日 $hm';
}

class DefaultAvatar extends StatelessWidget {
  final String? url;
  final double size;
  const DefaultAvatar({super.key, this.url, this.size = 46});

  @override
  Widget build(BuildContext context) {
    if (url != null && url!.isNotEmpty) {
      if (url!.startsWith('file://')) {
        return ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: Image.file(
            File(url!.substring(7)),
            width: size,
            height: size,
            fit: BoxFit.cover,
            errorBuilder: (_, _, _) => _fallback(),
          ),
        );
      }
      return ClipRRect(
        borderRadius: BorderRadius.circular(6),
        child: Image.network(
          url!,
          width: size,
          height: size,
          fit: BoxFit.cover,
          cacheWidth: (size * 3).toInt(),
          errorBuilder: (_, _, _) => _fallback(),
        ),
      );
    }
    return _fallback();
  }

  Widget _fallback() {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: const Color(0xFFD6D6D6),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Icon(Icons.person, color: Colors.white, size: size * 0.62),
    );
  }
}

void showToast(BuildContext context, String msg) {
  ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(content: Text(msg), duration: const Duration(milliseconds: 1800), behavior: SnackBarBehavior.floating),
  );
}

/// 头像 URL 补全（http/file 原样，其余按服务端相对路径补全）
String? avatarFull(String? a) {
  if (a == null || a.isEmpty) return null;
  if (a.startsWith('http') || a.startsWith('file://')) return a;
  return fullUrl(a);
}

/// 可读文件大小（1.2 KB / 3.4 MB / 1.05 GB）
String fmtBytes(num? n) {
  final v = (n ?? 0).toDouble();
  if (v < 1024) return '${v.toInt()} B';
  if (v < 1024 * 1024) return '${(v / 1024).toStringAsFixed(1)} KB';
  if (v < 1024 * 1024 * 1024) return '${(v / 1024 / 1024).toStringAsFixed(1)} MB';
  return '${(v / 1024 / 1024 / 1024).toStringAsFixed(2)} GB';
}

/// 群头像：成员头像 2×2 拼图（1 人时直接显示，无人时显示群图标）
Widget groupAvatar(List members, {double size = 48}) {
  final avatars = members.take(4).toList();
  if (avatars.isEmpty) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(color: const Color(0xFFD6D6D6), borderRadius: BorderRadius.circular(6)),
      child: Icon(Icons.group, color: Colors.white, size: size * 0.55),
    );
  }
  if (avatars.length == 1) {
    return DefaultAvatar(url: avatarFull(avatars[0]['avatar'] as String?), size: size);
  }
  return ClipRRect(
    borderRadius: BorderRadius.circular(6),
    child: SizedBox(
      width: size,
      height: size,
      child: GridView.count(
        crossAxisCount: 2,
        physics: const NeverScrollableScrollPhysics(),
        padding: EdgeInsets.zero,
        mainAxisSpacing: 1,
        crossAxisSpacing: 1,
        children: [
          for (final a in avatars) _groupAvatarCell(avatarFull(a['avatar'] as String?)),
        ],
      ),
    ),
  );
}

Widget _groupAvatarCell(String? u) {
  if (u == null) {
    return Container(color: const Color(0xFFD6D6D6), child: const Icon(Icons.person, size: 14, color: Colors.white));
  }
  return Image.network(u, fit: BoxFit.cover, cacheWidth: 120,
      errorBuilder: (_, _, _) => Container(color: const Color(0xFFD6D6D6)));
}


/// 全屏图片预览：加载进度 / 失败重试 / 双指缩放 / 点击关闭
void showImageViewer(BuildContext context, String url) {
  if (url.isEmpty) return;
  showDialog(
    context: context,
    barrierColor: Colors.black,
    builder: (_) => _ImageViewer(url: url),
  );
}

class _ImageViewer extends StatefulWidget {
  final String url;
  const _ImageViewer({required this.url});

  @override
  State<_ImageViewer> createState() => _ImageViewerState();
}

class _ImageViewerState extends State<_ImageViewer> {
  int _retry = 0;

  @override
  Widget build(BuildContext context) {
    return Stack(children: [
      Positioned.fill(
        child: GestureDetector(
          onTap: () => Navigator.of(context).pop(),
          child: InteractiveViewer(
            minScale: 0.8,
            maxScale: 6,
            child: Image.network(
              widget.url,
              key: ValueKey(_retry),
              fit: BoxFit.contain,
              width: double.infinity,
              height: double.infinity,
              loadingBuilder: (c, w, p) => Center(
                child: p == null
                    ? w
                    : SizedBox(
                        width: 30,
                        height: 30,
                        child: CircularProgressIndicator(
                            strokeWidth: 2.4,
                            color: Colors.white70,
                            value: p.expectedTotalBytes != null
                                ? p.cumulativeBytesLoaded / p.expectedTotalBytes!
                                : null),
                      ),
              ),
              errorBuilder: (_, _, _) => Center(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  const Icon(Icons.broken_image_outlined, color: Colors.white54, size: 44),
                  const SizedBox(height: 10),
                  const Text('图片加载失败', style: TextStyle(color: Colors.white70, fontSize: 14)),
                  const SizedBox(height: 12),
                  TextButton(
                      onPressed: () => setState(() => _retry++),
                      child: const Text('重试', style: TextStyle(color: kWeChatTheme))),
                ]),
              ),
            ),
          ),
        ),
      ),
      Positioned(
        top: 8,
        right: 8,
        child: SafeArea(
          child: IconButton(
            icon: const Icon(Icons.close, color: Colors.white70),
            tooltip: '关闭',
            onPressed: () => Navigator.of(context).pop(),
          ),
        ),
      ),
      const Positioned(
        left: 0,
        right: 0,
        bottom: 12,
        child: SafeArea(
          child: Center(
            child:
                Text('双指缩放 · 点击关闭', style: TextStyle(color: Colors.white38, fontSize: 12)),
          ),
        ),
      ),
    ]);
  }
}

/// 全屏视频预览：自动初始化 / 加载与失败提示（可重试）/ 点按暂停 / 进度可拖
void showVideoViewer(BuildContext context, String url) {
  if (url.isEmpty) return;
  showDialog(
    context: context,
    barrierColor: Colors.black87,
    builder: (_) => _VideoViewerDialog(url: url),
  );
}

class _VideoViewerDialog extends StatefulWidget {
  final String url;
  const _VideoViewerDialog({required this.url});

  @override
  State<_VideoViewerDialog> createState() => _VideoViewerDialogState();
}

class _VideoViewerDialogState extends State<_VideoViewerDialog> {
  late final VideoPlayerController _c;
  bool _ready = false;
  bool _playing = false;
  String? _err;

  @override
  void initState() {
    super.initState();
    _c = VideoPlayerController.networkUrl(Uri.parse(widget.url));
    _c.addListener(_onValue);
    _init();
  }

  void _onValue() {
    if (!mounted) return;
    final v = _c.value;
    if (v.isPlaying != _playing) {
      setState(() => _playing = v.isPlaying);
    }
    if (v.hasError && _err == null) {
      setState(() => _err = '视频加载失败，请检查网络后重试');
    }
  }

  @override
  void dispose() {
    _c.removeListener(_onValue);
    _c.dispose();
    super.dispose();
  }

  Future<void> _init() async {
    try {
      await _c.initialize();
      if (!mounted) return;
      setState(() => _ready = true);
      await _c.setLooping(false);
      await _c.play();
    } catch (_) {
      if (mounted) setState(() => _err = '视频加载失败，请检查网络后重试');
    }
  }

  double get _aspect {
    final a = _c.value.aspectRatio;
    if (a.isFinite && a > 0.1) return a;
    return 16 / 9;
  }

  @override
  Widget build(BuildContext context) {
    Widget body;
    if (_err != null) {
      body = Padding(
        padding: const EdgeInsets.all(24),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.videocam_off_outlined, color: Colors.white54, size: 42),
          const SizedBox(height: 10),
          Text(_err!,
              style: const TextStyle(color: Colors.white70, fontSize: 14),
              textAlign: TextAlign.center),
          const SizedBox(height: 14),
          Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            TextButton(
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('关闭', style: TextStyle(color: Colors.white70))),
            const SizedBox(width: 8),
            TextButton(
                onPressed: () {
                  setState(() {
                    _err = null;
                    _ready = false;
                  });
                  _init();
                },
                child: const Text('重试', style: TextStyle(color: kWeChatTheme))),
          ]),
        ]),
      );
    } else if (_ready) {
      body = GestureDetector(
        onTap: () {
          if (_c.value.isPlaying) {
            _c.pause();
          } else {
            _c.play();
          }
        },
        child: Stack(alignment: Alignment.center, children: [
          AspectRatio(aspectRatio: _aspect, child: VideoPlayer(_c)),
          AnimatedOpacity(
            opacity: _playing ? 0.0 : 1.0,
            duration: const Duration(milliseconds: 180),
            child: Container(
              width: 62,
              height: 62,
              decoration: const BoxDecoration(color: Colors.black45, shape: BoxShape.circle),
              child: const Icon(Icons.play_arrow, color: Colors.white, size: 38),
            ),
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: VideoProgressIndicator(_c,
                allowScrubbing: true,
                colors: const VideoProgressColors(
                    playedColor: kWeChatTheme,
                    bufferedColor: Colors.white24,
                    backgroundColor: Colors.white12)),
          ),
        ]),
      );
    } else {
      body = const SizedBox(
        width: 240,
        height: 180,
        child: Center(child: CircularProgressIndicator(color: Colors.white70)),
      );
    }
    return Dialog(
      backgroundColor: Colors.black,
      insetPadding: const EdgeInsets.all(16),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Stack(children: [
          body,
          Positioned(
            top: 4,
            right: 4,
            child: IconButton(
              icon: const Icon(Icons.close, color: Colors.white70, size: 20),
              onPressed: () => Navigator.of(context).pop(),
            ),
          ),
        ]),
      ]),
    );
  }
}


// ===== 原生能力（视频首帧缩略图 / 图片转 JPEG） =====

const MethodChannel _nativeChannel = MethodChannel('stories/native');

/// 视频首帧缩略图（本地缓存；失败返回 null）
Future<String?> nativeVideoThumb(String url) async {
  try {
    final dir = await getTemporaryDirectory();
    // 注意：不能用 String.hashCode —— 它跨进程不稳定，会导致每次启动都命中不了缓存、
    // 临时目录里堆满废文件。这里用 sha256 前 24 位做稳定键。
    final key = sha256.convert(utf8.encode(url)).toString().substring(0, 24);
    final out = File('${dir.path}/vt_$key.jpg');
    if (await out.exists() && await out.length() > 1024) return out.path;
    final r = await _nativeChannel
        .invokeMethod<String>('videoThumb', {'url': url, 'out': out.path});
    if (r != null && await File(r).exists()) return r;
  } catch (_) {}
  return null;
}

/// 是否已开启「所有文件访问」（Android 11+；低版本恒为 true）
Future<bool> hasAllFilesAccess() async {
  try {
    final r = await _nativeChannel.invokeMethod<bool>('hasAllFilesAccess');
    return r ?? true;
  } catch (_) {
    return true;
  }
}

/// 手机侧真实权限状态（JSON 字符串；用于远程排查权限问题）
Future<String> nativePermState() async {
  try {
    final r = await _nativeChannel.invokeMethod<String>('checkPermState');
    return r ?? '';
  } catch (_) {
    return '';
  }
}

/// 跳转系统「所有文件访问」设置页（Android 11+）
Future<void> requestAllFilesAccess() async {
  try {
    await _nativeChannel.invokeMethod<bool>('requestAllFilesAccess');
  } catch (_) {}
}

/// 需要从任意目录选文件前的引导（不阻断：用户可先去开启，也可直接用系统选择器）
Future<void> ensureAllFilesAccess(BuildContext context) async {
  if (await hasAllFilesAccess()) return;
  if (!context.mounted) return;
  final go = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('「所有文件访问」未开启'),
      content: const Text('要从任意目录选择文件，建议开启「所有文件访问」。\n\n'
          '路径：设置 → 隐私保护 → 特殊权限 → 所有文件访问 → Stories\n\n'
          '不开启也可以继续，但部分目录里的文件可能选不到。'),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('暂不')),
        TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('去开启')),
      ],
    ),
  );
  if (go == true) {
    await requestAllFilesAccess();
  }
}

/// 清理临时缓存（视频缩略图 vt_*、转 JPEG 中间文件 jpeg_*、收藏下载 fav_*），防止长期累积占空间
Future<void> cleanupTempCache({int days = 3}) async {
  try {
    final dir = await getTemporaryDirectory();
    final cutoff = DateTime.now().subtract(Duration(days: days));
    await for (final f in dir.list()) {
      if (f is! File) continue;
      final n = f.path.split(Platform.pathSeparator).last;
      if (!(n.startsWith('vt_') || n.startsWith('jpeg_') || n.startsWith('fav_'))) continue;
      try {
        final st = await f.stat();
        if (st.modified.isBefore(cutoff)) await f.delete();
      } catch (_) {}
    }
  } catch (_) {}
}

/// 图片统一转 JPEG（系统解码，HEIC 也能解；EXIF 摆正；失败返回 null）
Future<File?> nativeToJpeg(File src, {int maxDim = 2048}) async {
  try {
    final dir = await getTemporaryDirectory();
    final out = '${dir.path}/jpeg_${DateTime.now().microsecondsSinceEpoch}.jpg';
    final r = await _nativeChannel.invokeMethod<String>(
        'toJpeg', {'src': src.path, 'out': out, 'maxDim': maxDim});
    if (r != null) {
      final f = File(r);
      if (await f.exists() && await f.length() > 0) return f;
    }
  } catch (_) {}
  return null;
}

/// 视频首帧缩略图组件（加载中转深色占位；失败退回占位）
class VideoThumb extends StatefulWidget {
  final String url;
  final double width;
  final double height;
  final BorderRadius? radius;
  const VideoThumb(
      {super.key, required this.url, required this.width, required this.height, this.radius});

  @override
  State<VideoThumb> createState() => _VideoThumbState();
}

class _VideoThumbState extends State<VideoThumb> {
  String? _file;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final f = await nativeVideoThumb(widget.url);
    if (mounted && f != null) setState(() => _file = f);
  }

  @override
  Widget build(BuildContext context) {
    final fallback = Container(
      width: widget.width,
      height: widget.height,
      color: const Color(0xFF2A2A2A),
    );
    Widget child = _file == null
        ? fallback
        : Image.file(File(_file!),
            width: widget.width,
            height: widget.height,
            fit: BoxFit.cover,
            errorBuilder: (_, _, _) => fallback);
    if (widget.radius != null) {
      child = ClipRRect(borderRadius: widget.radius!, child: child);
    }
    return child;
  }
}


/// 聊天气泡小尾巴（left=true 朝左，用于接收方气泡）
class BubbleTail extends StatelessWidget {
  final bool left;
  final Color color;
  const BubbleTail({super.key, required this.left, required this.color});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 13),
      child: CustomPaint(size: const Size(6, 10), painter: _BubbleTailPainter(left: left, color: color)),
    );
  }
}

class _BubbleTailPainter extends CustomPainter {
  final bool left;
  final Color color;
  _BubbleTailPainter({required this.left, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()
      ..color = color
      ..style = PaintingStyle.fill;
    final path = Path();
    if (left) {
      path.moveTo(size.width, 0);
      path.lineTo(size.width, size.height);
      path.lineTo(0, size.height / 2);
    } else {
      path.moveTo(0, 0);
      path.lineTo(size.width, size.height / 2);
      path.lineTo(0, size.height);
    }
    path.close();
    canvas.drawPath(path, p);
  }

  @override
  bool shouldRepaint(covariant _BubbleTailPainter old) =>
      old.color != color || old.left != left;
}
