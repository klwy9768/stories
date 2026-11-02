import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../api.dart';
import '../util.dart';
import '../version.dart';

/// 小程序 WebView 容器（服务端托管 HTML + token 注入）
class MiniAppWebPage extends StatefulWidget {
  final Map<String, dynamic> app;
  const MiniAppWebPage({super.key, required this.app});

  @override
  State<MiniAppWebPage> createState() => _MiniAppWebPageState();
}

class _MiniAppWebPageState extends State<MiniAppWebPage> {
  late final WebViewController _ctl;
  int _progress = 0;
  bool _failed = false;

  /// 只允许站内（服务器）域名导航，阻止小程序内打开任意外链
  String get _allowedHost => Uri.tryParse(apiBase)?.host ?? '';

  String get _url {
    final id = '${widget.app['id']}';
    // fullUrl 会自动带上 token（小程序页 JS 从 query 读取，用于调用 /api）
    final base = fullUrl('/miniapps/$id/index.html');
    return '$base&v=${Uri.encodeComponent(kAppVersion)}';
  }

  @override
  void initState() {
    super.initState();
    _ctl = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0xFFF7F7F7))
      ..setNavigationDelegate(NavigationDelegate(
        onProgress: (p) {
          if (mounted) setState(() => _progress = p);
        },
        onWebResourceError: (e) {
          if (e.isForMainFrame == true && mounted) setState(() => _failed = true);
        },
        onNavigationRequest: (req) {
          final u = Uri.tryParse(req.url);
          final scheme = (u?.scheme ?? '').toLowerCase();
          final host = u?.host ?? '';
          // 只放行本站 https（localhost/模拟器地址例外，便于本地调试）；
          // data:/blob:/javascript:/file: 与明文 http 一律拦截
          final isLocal = host == 'localhost' || host == '127.0.0.1' || host == '10.0.2.2';
          final schemeOk = scheme == 'https' || (scheme == 'http' && isLocal);
          if (schemeOk && host == _allowedHost) {
            return NavigationDecision.navigate;
          }
          return NavigationDecision.prevent;
        },
      ))
      ..loadRequest(Uri.parse(_url));
  }

  void _reload() {
    setState(() {
      _failed = false;
      _progress = 0;
    });
    _ctl.loadRequest(Uri.parse(_url));
  }

  Future<void> _goBackOrClose() async {
    try {
      if (await _ctl.canGoBack()) {
        await _ctl.goBack();
        return;
      }
    } catch (_) {}
    if (mounted) Navigator.of(context).pop(); // 不能走 maybePop：canPop=false 时会再次回调 onPopInvoked 形成死循环
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
          title: Text('${widget.app['name'] ?? '小程序'}'),
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
                      const Text('小程序加载失败，请检查网络', style: TextStyle(color: Colors.grey)),
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
