import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:video_player/video_player.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../api.dart';
import '../log.dart';
import '../util.dart';

/// 看一看：每日新闻（学术/体育/军事/政治/国际；每天 6:30 后自动更新）
class NewsPage extends StatefulWidget {
  const NewsPage({super.key});

  @override
  State<NewsPage> createState() => _NewsPageState();
}

class _NewsPageState extends State<NewsPage> {
  List<Map<String, dynamic>> _days = [];
  bool _loading = true;
  bool _fetching = false;
  Timer? _poll;
  int _pollCount = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final d = await Api.news();
      final days =
          ((d['days'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
      final fetching = d['fetching'] == true;
      if (!mounted) return;
      setState(() {
        _days = days;
        _loading = false;
        _fetching = fetching;
      });
      if (fetching) _startPoll();
    } catch (e) {
      Log.error('news', '加载新闻失败: $e');
      if (mounted) setState(() => _loading = false);
    }
  }

  void _startPoll() {
    _poll?.cancel();
    _pollCount = 0;
    var inflight = false;
    _poll = Timer.periodic(const Duration(seconds: 4), (t) async {
      if (inflight) return; // 防重入：上一轮请求未返回则跳过本次
      inflight = true;
      try {
        _pollCount++;
        if (_pollCount > 90) {
          t.cancel();
          if (mounted) {
            setState(() => _fetching = false);
            showToast(context, '更新耗时较长，稍后下拉刷新看看');
          }
          return;
        }
        final d = await Api.news();
        if (d['fetching'] == true) return;
        t.cancel();
        final days =
            ((d['days'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
        if (!mounted) return;
        setState(() {
          _days = days;
          _fetching = false;
        });
        showToast(context, '已更新');
      } catch (_) {
      } finally {
        inflight = false;
      }
    });
  }

  Future<void> _refresh() async {
    if (_fetching) {
      showToast(context, '正在更新中…');
      return;
    }
    try {
      final r = await Api.newsRefresh();
      if (r['skipped'] == true) {
        if (mounted) showToast(context, '刚刚已更新过');
        await _load();
        return;
      }
      if (r['ok'] != true) {
        if (mounted) showToast(context, '${r['error'] ?? '暂时无法更新'}');
        return;
      }
      if (mounted) {
        setState(() => _fetching = true);
        showToast(context, '正在抓取最新新闻…');
      }
      _startPoll();
    } catch (_) {
      if (mounted) showToast(context, '刷新失败');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('看一看'),
        actions: [
          if (_fetching)
            const Padding(
              padding: EdgeInsets.only(right: 16),
              child: Center(
                  child: SizedBox(
                      width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))),
            )
          else
            IconButton(icon: const Icon(Icons.refresh), tooltip: '刷新', onPressed: _refresh),
          const SizedBox(width: 4),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _days.isEmpty
              ? const Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Text('还没有新闻', style: TextStyle(color: Colors.grey, fontSize: 15)),
                    SizedBox(height: 8),
                    Text('每天 6:30 后自动更新，点右上角可手动刷新',
                        style: TextStyle(color: Color(0xFFAAAAAA), fontSize: 12)),
                  ]),
                )
              : RefreshIndicator(
                  color: kWeChatTheme,
                  onRefresh: _load,
                  child: ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.only(bottom: 20),
                    children: [
                      for (final day in _days) ...[
                        Padding(
                          padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
                          child: Text(_dayLabel('${day['day'] ?? ''}'),
                              style: const TextStyle(fontSize: 13, color: Color(0xFF999999))),
                        ),
                        for (final cat in ((day['categories'] as List?) ?? []))
                          _categoryBlock((cat as Map).cast<String, dynamic>()),
                      ],
                    ],
                  ),
                ),
    );
  }

  Widget _categoryBlock(Map<String, dynamic> cat) {
    final name = '${cat['name'] ?? ''}';
    final items =
        ((cat['items'] as List?) ?? []).map((e) => (e as Map).cast<String, dynamic>()).toList();
    return Container(
      color: Colors.white,
      margin: const EdgeInsets.only(bottom: 10),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 2),
          child: Row(children: [
            Container(
                width: 3,
                height: 14,
                decoration: BoxDecoration(
                    color: _catColor(name), borderRadius: BorderRadius.circular(2))),
            const SizedBox(width: 8),
            Text(name, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
          ]),
        ),
        for (var i = 0; i < items.length; i++) ...[
          if (i > 0) const Divider(height: 1, indent: 16, endIndent: 16),
          _newsRow(items[i], items),
        ],
      ]),
    );
  }

  Widget _newsRow(Map<String, dynamic> it, List<Map<String, dynamic>> siblings) {
    final img = '${it['image'] ?? ''}';
    final hasVideo = '${it['video'] ?? ''}'.isNotEmpty;
    final source = '${it['source'] ?? ''}';
    return InkWell(
      onTap: () => Navigator.push(
          context, MaterialPageRoute(builder: (_) => _NewsDetailPage(item: it, siblings: siblings))),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${it['title'] ?? ''}',
                  style: const TextStyle(fontSize: 15.5, height: 1.35),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis),
              const SizedBox(height: 6),
              Row(children: [
                Flexible(
                  child: Text(source,
                      style: const TextStyle(fontSize: 12, color: Color(0xFF999999)),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis),
                ),
                if (hasVideo) ...[
                  const SizedBox(width: 6),
                  const Icon(Icons.play_circle_outline, size: 13, color: kWeChatTheme),
                  const SizedBox(width: 2),
                  const Text('视频', style: TextStyle(fontSize: 11.5, color: kWeChatTheme)),
                ],
              ]),
            ]),
          ),
          const SizedBox(width: 10),
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: SizedBox(
              width: 96,
              height: 68,
              child: Stack(children: [
                Positioned.fill(
                  child: img.isEmpty
                      ? _imgPh()
                      : Image.network(fullUrl(img),
                          fit: BoxFit.cover,
                          cacheWidth: 288,
                          errorBuilder: (_, _, _) => _imgPh()),
                ),
                if ('${it['video'] ?? ''}'.isNotEmpty)
                  const Center(
                      child: Icon(Icons.play_circle_fill, color: Colors.white70, size: 28)),
              ]),
            ),
          ),
        ]),
      ),
    );
  }

  Widget _imgPh() {
    return Container(
      color: const Color(0xFFF2F2F2),
      child: const Icon(Icons.image_not_supported_outlined, size: 20, color: Color(0xFFBBBBBB)),
    );
  }
}

class _NewsDetailPage extends StatelessWidget {
  final Map<String, dynamic> item;
  final List<Map<String, dynamic>> siblings;
  const _NewsDetailPage({required this.item, this.siblings = const []});

  @override
  Widget build(BuildContext context) {
    final title = '${item['title'] ?? ''}';
    final summary = '${item['summary'] ?? ''}';
    final content = '${item['content'] ?? ''}'.trim();
    final source = '${item['source'] ?? ''}';
    final image = '${item['image'] ?? ''}';
    final video = '${item['video'] ?? ''}';
    final url = '${item['url'] ?? ''}';
    final body = content.isNotEmpty ? content : summary;
    final related = siblings
        .where((e) => '${e['url'] ?? ''}' != url && '${e['title'] ?? ''}'.isNotEmpty)
        .take(5)
        .toList();
    return Scaffold(
      appBar: AppBar(title: const Text('看一看')),
      body: ListView(padding: const EdgeInsets.fromLTRB(16, 14, 16, 30), children: [
        Text(title,
            style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w600, height: 1.4)),
        const SizedBox(height: 10),
        Row(children: [
          Text(source, style: const TextStyle(fontSize: 12.5, color: Color(0xFF576B95))),
          const SizedBox(width: 10),
          Text(fmtTime((item['ts'] as num?)?.toDouble()),
              style: const TextStyle(fontSize: 12.5, color: Color(0xFF999999))),
        ]),
        const SizedBox(height: 14),
        if (video.isNotEmpty) ...[
          _InlineNewsVideo(url: fullUrl(video)),
          const SizedBox(height: 16),
        ] else if (image.isNotEmpty) ...[
          ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: AspectRatio(
              aspectRatio: 16 / 9,
              child: Image.network(fullUrl(image),
                  fit: BoxFit.cover,
                  cacheWidth: 1080,
                  errorBuilder: (_, _, _) => Container(
                        color: const Color(0xFFF2F2F2),
                        child: const Icon(Icons.broken_image_outlined,
                            color: Color(0xFFBBBBBB)),
                      )),
            ),
          ),
          const SizedBox(height: 16),
        ],
        if (body.isNotEmpty)
          Text(body, style: const TextStyle(fontSize: 15.5, height: 1.75, color: Color(0xFF333333))),
        if (url.isNotEmpty) ...[
          const SizedBox(height: 22),
          Row(children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: () => Navigator.push(context,
                    MaterialPageRoute(builder: (_) => _NewsOriginalPage(url: url, title: source))),
                icon: const Icon(Icons.open_in_new, size: 18),
                label: const Text('查看原文'),
                style: OutlinedButton.styleFrom(
                    foregroundColor: kWeChatTheme,
                    side: const BorderSide(color: kWeChatTheme)),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: OutlinedButton.icon(
                onPressed: () async {
                  await Clipboard.setData(ClipboardData(text: url));
                  if (context.mounted) showToast(context, '链接已复制');
                },
                icon: const Icon(Icons.link, size: 18),
                label: const Text('复制链接'),
                style: OutlinedButton.styleFrom(
                    foregroundColor: const Color(0xFF666666),
                    side: const BorderSide(color: Color(0xFFDDDDDD))),
              ),
            ),
          ]),
        ],
        if (related.isNotEmpty) ...[
          const SizedBox(height: 26),
          const Text('相关推荐', style: TextStyle(fontSize: 14.5, fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          for (final r in related)
            InkWell(
              onTap: () => Navigator.push(context, MaterialPageRoute(
                  builder: (_) => _NewsDetailPage(item: r, siblings: siblings))),
              child: Container(
                padding: const EdgeInsets.symmetric(vertical: 12),
                decoration: const BoxDecoration(
                    border: Border(bottom: BorderSide(color: Color(0xFFEFEFEF)))),
                child: Row(children: [
                  Expanded(
                    child: Text('${r['title'] ?? ''}',
                        style: const TextStyle(fontSize: 14.5, height: 1.35),
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis),
                  ),
                  const SizedBox(width: 10),
                  Text('${r['source'] ?? ''}',
                      style: const TextStyle(fontSize: 11.5, color: Color(0xFF999999))),
                ]),
              ),
            ),
        ],
        const SizedBox(height: 24),
        const Center(
            child: Text('内容来自网络媒体，版权归原作者所有',
                style: TextStyle(fontSize: 11.5, color: Color(0xFFAAAAAA)))),
      ]),
    );
  }
}

/// 只允许打开公网 http/https；拦截 file/data/javascript/blob 等危险 scheme 与内网/环回地址。
/// WebView 会跟随页面内跳转，因此导航回调里也要逐跳校验。
bool _isSafeWebUrl(String raw) {
  final u = Uri.tryParse(raw.trim());
  if (u == null) return false;
  final scheme = u.scheme.toLowerCase();
  if (scheme != 'http' && scheme != 'https') return false;
  final host = u.host.toLowerCase();
  if (host.isEmpty) return false;
  if (host == 'localhost' || host == '0.0.0.0' || host == '::1') return false;
  final m = RegExp(r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$').firstMatch(host);
  if (m != null) {
    final a = int.parse(m.group(1)!);
    final b = int.parse(m.group(2)!);
    if (a == 10 || a == 127 || (a == 169 && b == 254) ||
        (a == 192 && b == 168) || (a == 172 && b >= 16 && b <= 31)) {
      return false;
    }
  }
  return true;
}

/// 原文页：内嵌 WebView 打开新闻原站（可返回上一页）
class _NewsOriginalPage extends StatefulWidget {
  final String url;
  final String title;
  const _NewsOriginalPage({required this.url, this.title = '原文'});

  @override
  State<_NewsOriginalPage> createState() => _NewsOriginalPageState();
}

class _NewsOriginalPageState extends State<_NewsOriginalPage> {
  late final WebViewController _ctl;
  int _progress = 0;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    final safe = _isSafeWebUrl(widget.url);
    if (!safe) _failed = true;
    _ctl = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(Colors.white)
      ..setNavigationDelegate(NavigationDelegate(
        // 只放行公网 http/https；file:/data:/javascript:/blob: 与内网地址一律拦截
        onNavigationRequest: (req) => _isSafeWebUrl(req.url)
            ? NavigationDecision.navigate
            : NavigationDecision.prevent,
        onProgress: (p) {
          if (mounted) setState(() => _progress = p);
        },
        onWebResourceError: (e) {
          if (e.isForMainFrame == true && mounted) setState(() => _failed = true);
        },
      ));
    if (safe) _ctl.loadRequest(Uri.parse(widget.url));
  }

  void _reload() {
    if (!_isSafeWebUrl(widget.url)) {
      setState(() => _failed = true);
      return;
    }
    setState(() {
      _failed = false;
      _progress = 0;
    });
    _ctl.loadRequest(Uri.parse(widget.url));
  }

  Future<void> _goBackOrClose() async {
    try {
      if (await _ctl.canGoBack()) {
        await _ctl.goBack();
        return;
      }
    } catch (_) {}
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (didPop) return;
        _goBackOrClose();
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text(widget.title, maxLines: 1, overflow: TextOverflow.ellipsis),
          actions: [
            IconButton(icon: const Icon(Icons.refresh, size: 22), tooltip: '刷新', onPressed: _reload),
            const SizedBox(width: 4),
          ],
        ),
        body: Column(children: [
          if (_progress < 100 && !_failed)
            LinearProgressIndicator(value: _progress > 0 ? _progress / 100 : null, minHeight: 2),
          Expanded(
            child: _failed
                ? Center(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                      const Text('页面加载失败，请检查网络', style: TextStyle(color: Colors.grey)),
                      const SizedBox(height: 12),
                      FilledButton(onPressed: _reload, child: const Text('重试')),
                    ]),
                  )
                : WebViewWidget(controller: _ctl),
          ),
        ]),
      ),
    );
  }
}

String _dayLabel(String day) {
  final now = DateTime.now();
  String fmt(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
  if (day == fmt(now)) return '今天';
  if (day == fmt(now.subtract(const Duration(days: 1)))) return '昨天';
  final p = day.split('-');
  if (p.length == 3) return '${int.tryParse(p[1]) ?? p[1]}月${int.tryParse(p[2]) ?? p[2]}日';
  return day;
}

Color _catColor(String name) {
  switch (name) {
    case '学术':
      return const Color(0xFF2782D7);
    case '体育':
      return const Color(0xFF07C160);
    case '军事':
      return const Color(0xFF5C6BC0);
    case '政治':
      return const Color(0xFFFA5151);
    case '国际':
      return const Color(0xFF9B59B6);
    default:
      return const Color(0xFF888888);
  }
}

/// 新闻页内嵌视频（本页直接播放，不跳转）
class _InlineNewsVideo extends StatefulWidget {
  final String url;
  const _InlineNewsVideo({required this.url});

  @override
  State<_InlineNewsVideo> createState() => _InlineNewsVideoState();
}

class _InlineNewsVideoState extends State<_InlineNewsVideo> {
  VideoPlayerController? _c;
  bool _ready = false;
  bool _playing = false;
  String? _err;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    try {
      final old = _c;
      if (old != null) {
        old.removeListener(_onValue);
        try {
          await old.dispose();
        } catch (_) {}
        _c = null;
      }
      final c = VideoPlayerController.networkUrl(Uri.parse(widget.url));
      _c = c;
      c.addListener(_onValue);
      await c.initialize();
      if (!mounted) return;
      setState(() => _ready = true);
    } catch (_) {
      if (mounted) setState(() => _err = '视频加载失败');
    }
  }

  void _onValue() {
    final c = _c;
    if (c == null || !mounted) return;
    if (c.value.isPlaying != _playing) {
      setState(() => _playing = c.value.isPlaying);
    }
    if (c.value.hasError && _err == null) {
      setState(() => _err = '视频加载失败');
    }
  }

  @override
  void dispose() {
    _c?.removeListener(_onValue);
    _c?.dispose();
    super.dispose();
  }

  void _toggle() {
    final c = _c;
    if (c == null || !_ready) return;
    if (c.value.isPlaying) {
      c.pause();
    } else {
      c.play();
    }
  }

  @override
  Widget build(BuildContext context) {
    Widget body;
    if (_err != null) {
      body = SizedBox(
        height: 180,
        child: Center(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.videocam_off_outlined, color: Colors.white54, size: 36),
            const SizedBox(height: 8),
            Text(_err!, style: const TextStyle(color: Colors.white70, fontSize: 13)),
            const SizedBox(height: 6),
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
        ),
      );
    } else if (_ready) {
      final a = _c!.value.aspectRatio;
      final aspect = (a.isFinite && a > 0.1) ? a : 16 / 9;
      body = GestureDetector(
        onTap: _toggle,
        child: Stack(alignment: Alignment.center, children: [
          AspectRatio(aspectRatio: aspect, child: VideoPlayer(_c!)),
          AnimatedOpacity(
            opacity: _playing ? 0.0 : 1.0,
            duration: const Duration(milliseconds: 180),
            child: Container(
              width: 58,
              height: 58,
              decoration: const BoxDecoration(color: Colors.black45, shape: BoxShape.circle),
              child: const Icon(Icons.play_arrow, color: Colors.white, size: 36),
            ),
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: VideoProgressIndicator(_c!,
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
        height: 180,
        child: Center(child: CircularProgressIndicator(strokeWidth: 2.2)),
      );
    }
    return ClipRRect(
      borderRadius: BorderRadius.circular(8),
      child: Container(color: Colors.black, child: body),
    );
  }
}
