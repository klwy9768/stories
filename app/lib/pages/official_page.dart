import 'package:flutter/material.dart';

import '../api.dart';
import '../secrets.dart';
import '../update.dart';
import '../util.dart';
import 'license_page.dart';

/// Stories 官方（通讯录 → 公众号）：版本信息 / 检查更新 / 开源许可
class OfficialPage extends StatefulWidget {
  const OfficialPage({super.key});

  @override
  State<OfficialPage> createState() => _OfficialPageState();
}

class _OfficialPageState extends State<OfficialPage> with WidgetsBindingObserver {
  Map<String, dynamic>? _info;
  bool _allFiles = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _fetchAllFiles();
    Api.appVersion().then((v) {
      if (mounted) setState(() => _info = v);
    }).catchError((_) {});
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _fetchAllFiles();
  }

  Future<void> _fetchAllFiles() async {
    final v = await hasAllFilesAccess();
    if (mounted && v != _allFiles) setState(() => _allFiles = v);
  }

  Future<void> _editBase() async {
    final c = TextEditingController(text: apiBase);
    final v = await showDialog<String>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('服务器地址'),
        content: TextField(
            controller: c,
            keyboardType: TextInputType.url,
            decoration: const InputDecoration(hintText: 'https://主机:端口')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('取消')),
          TextButton(onPressed: () => Navigator.pop(context, c.text.trim()), child: const Text('保存')),
        ],
      ),
    );
    if (v != null && v.isNotEmpty) {
      await Api.saveBase(v);
      if (mounted) {
        setState(() {});
        showToast(context, '已保存（重启 App 后完全生效）');
      }
    }
  }

  Future<void> _editToken() async {
    final c = TextEditingController(text: appToken);
    final v = await showDialog<String>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('访问令牌'),
        content: TextField(
            controller: c,
            obscureText: true,
            decoration: const InputDecoration(hintText: 'X-App-Token')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('取消')),
          TextButton(onPressed: () => Navigator.pop(context, c.text.trim()), child: const Text('保存')),
        ],
      ),
    );
    if (v != null && v.isNotEmpty) {
      await Api.saveToken(v);
      if (mounted) {
        setState(() {});
        showToast(context, '令牌已更新');
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final v = _info;
    final notes = '${v?['notes'] ?? ''}'.trim();
    return Scaffold(
      appBar: AppBar(title: const Text('Stories 官方')),
      body: ListView(children: [
        Container(
          color: Colors.white,
          padding: const EdgeInsets.fromLTRB(16, 18, 16, 18),
          child: Row(children: [
            Container(
              width: 52,
              height: 52,
              decoration: BoxDecoration(color: kWeChatTheme, borderRadius: BorderRadius.circular(10)),
              child: const Icon(Icons.chat_bubble, color: Colors.white, size: 28),
            ),
            const SizedBox(width: 14),
            const Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('Stories', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
                SizedBox(height: 4),
                Text('私人自用的 AI 角色聊天应用', style: TextStyle(fontSize: 13, color: Color(0xFF999999))),
              ]),
            ),
          ]),
        ),
        const SizedBox(height: 10),
        Container(
          color: Colors.white,
          child: Column(children: [
            ListTile(
              title: const Text('当前版本', style: TextStyle(fontSize: 16)),
              trailing: Text('v${v?['version'] ?? '?'}', style: const TextStyle(fontSize: 15, color: Color(0xFF999999))),
            ),
            if (notes.isNotEmpty)
              ListTile(
                title: const Text('更新说明', style: TextStyle(fontSize: 16)),
                subtitle: Text(notes, style: const TextStyle(fontSize: 13, color: Color(0xFF999999))),
              ),
            ListTile(
              title: const Text('检查更新', style: TextStyle(fontSize: 16, color: Color(0xFF576B95))),
              trailing: const Icon(Icons.refresh, size: 20, color: Color(0xFFC7C7C7)),
              onTap: () => checkAppUpdate(context, manual: true),
            ),
            ListTile(
              title: const Text('开源许可', style: TextStyle(fontSize: 16)),
              trailing: const Icon(Icons.chevron_right, size: 20, color: Color(0xFFC7C7C7)),
              onTap: () => Navigator.push(
                  context, MaterialPageRoute(builder: (_) => const OpenSourceLicensePage())),
            ),
            ListTile(
              title: const Text('服务器', style: TextStyle(fontSize: 16)),
              subtitle: Text(apiBase, style: const TextStyle(fontSize: 12, color: Color(0xFF999999))),
              trailing: const Icon(Icons.edit, size: 18, color: Color(0xFFC7C7C7)),
              onTap: _editBase,
            ),
            ListTile(
              title: const Text('访问令牌', style: TextStyle(fontSize: 16)),
              subtitle: Text(
                  appToken.isEmpty
                      ? '（未配置）'
                      : (appToken.length > 8
                          ? '${appToken.substring(0, 4)}****${appToken.substring(appToken.length - 4)}'
                          : '（已配置）'),
                  style: const TextStyle(fontSize: 12, color: Color(0xFF999999))),
              trailing: const Icon(Icons.edit, size: 18, color: Color(0xFFC7C7C7)),
              onTap: _editToken,
            ),
            ListTile(
              title: const Text('所有文件访问', style: TextStyle(fontSize: 16)),
              subtitle: Text(
                  _allFiles ? '已开启（可从任意目录选择文件）' : '未开启（收藏上传时部分目录可能选不到文件）',
                  style: const TextStyle(fontSize: 12, color: Color(0xFF999999))),
              trailing: _allFiles
                  ? const Icon(Icons.check_circle, size: 18, color: kWeChatTheme)
                  : const Icon(Icons.chevron_right, size: 18, color: Color(0xFFC7C7C7)),
              onTap: _allFiles
                  ? null
                  : () async {
                      await requestAllFilesAccess();
                    },
            ),
          ]),
        ),
        const SizedBox(height: 20),
      ]),
    );
  }
}
