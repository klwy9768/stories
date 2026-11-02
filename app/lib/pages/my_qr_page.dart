import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:photo_manager/photo_manager.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../log.dart';
import '../user_profile.dart';
import '../util.dart';

/// 「我的二维码」页（二维码内容为本机身份标识，供展示/分享用）
class MyQrPage extends StatefulWidget {
  const MyQrPage({super.key});

  @override
  State<MyQrPage> createState() => _MyQrPageState();
}

class _MyQrPageState extends State<MyQrPage> {
  final GlobalKey _qrKey = GlobalKey();
  UserProfile? _p;
  bool _green = false;

  @override
  void initState() {
    super.initState();
    UserProfile.load().then((p) {
      if (mounted) setState(() => _p = p);
    });
  }

  Future<void> _save() async {
    try {
      final ps = await PhotoManager.requestPermissionExtend();
      if (!ps.hasAccess) {
        if (mounted) showToast(context, '没有相册权限');
        return;
      }
      final boundary = _qrKey.currentContext!.findRenderObject() as RenderRepaintBoundary;
      final img = await boundary.toImage(pixelRatio: 3);
      final bytes = await img.toByteData(format: ui.ImageByteFormat.png);
      img.dispose(); // 释放位图，避免反复保存持续泄漏
      if (bytes == null) return;
      await PhotoManager.editor.saveImage(
        bytes.buffer.asUint8List(),
        filename: 'stories_qr_${DateTime.now().millisecondsSinceEpoch}.png',
      );
      if (mounted) showToast(context, '已保存到相册');
    } catch (e) {
      Log.error('qr', 'save image failed: $e');
      if (mounted) showToast(context, '保存失败，请重试');
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = _p;
    final fg = _green ? Colors.white : const Color(0xFF1A1A1A);
    final bg = _green ? kWeChatTheme : Colors.white;
    return Scaffold(
      appBar: AppBar(title: const Text('我的二维码')),
      body: p == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(padding: const EdgeInsets.fromLTRB(12, 12, 12, 20), children: [
              Container(
                decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(10)),
                padding: const EdgeInsets.fromLTRB(18, 14, 18, 20),
                child: Column(children: [
                  Row(children: [
                    const Icon(Icons.person_add_alt, size: 22, color: Color(0xFFFA9D3B)),
                    const SizedBox(width: 8),
                    const Expanded(
                      child: Text('添加我为朋友', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
                    ),
                    const Icon(Icons.more_horiz, size: 22, color: Color(0xFF888888)),
                    const SizedBox(width: 10),
                    const Icon(Icons.keyboard_arrow_up, size: 22, color: Color(0xFF888888)),
                  ]),
                  const SizedBox(height: 18),
                  Row(children: [
                    DefaultAvatar(url: p.avatar.isEmpty ? null : p.avatar, size: 48),
                    const SizedBox(width: 12),
                    Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(p.name, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
                      const SizedBox(height: 4),
                      Text(p.region.isEmpty ? 'Stories' : p.region,
                          style: const TextStyle(fontSize: 13, color: Color(0xFF999999))),
                    ]),
                  ]),
                  const SizedBox(height: 26),
                  RepaintBoundary(
                    key: _qrKey,
                    child: Container(
                      color: bg,
                      padding: const EdgeInsets.all(14),
                      child: QrImageView(
                        data: 'stories://user/${p.wxid}',
                        version: QrVersions.auto,
                        size: 210,
                        backgroundColor: bg,
                        eyeStyle: QrEyeStyle(eyeShape: QrEyeShape.square, color: fg),
                        dataModuleStyle: QrDataModuleStyle(
                            dataModuleShape: QrDataModuleShape.square, color: fg),
                      ),
                    ),
                  ),
                  const SizedBox(height: 22),
                  const Text('扫二维码，添加我为朋友。',
                      style: TextStyle(fontSize: 14, color: Color(0xFF999999))),
                  const SizedBox(height: 24),
                  Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                    GestureDetector(
                      onTap: () => showToast(context, '活动未开放'),
                      child: const Text('扫一扫', style: TextStyle(fontSize: 15, color: Color(0xFF576B95))),
                    ),
                    const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 14),
                      child: Text('|', style: TextStyle(color: Color(0xFFDDDDDD))),
                    ),
                    GestureDetector(
                      onTap: () => setState(() => _green = !_green),
                      child: const Text('换个样式',
                          style: TextStyle(fontSize: 15, color: Color(0xFF576B95))),
                    ),
                    const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 14),
                      child: Text('|', style: TextStyle(color: Color(0xFFDDDDDD))),
                    ),
                    GestureDetector(
                      onTap: _save,
                      child: const Text('保存图片',
                          style: TextStyle(fontSize: 15, color: Color(0xFF576B95))),
                    ),
                  ]),
                ]),
              ),
              const SizedBox(height: 12),
              Container(
                decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(10)),
                child: ListTile(
                  leading: const Icon(Icons.swap_horiz, color: Color(0xFF2782D7)),
                  title: const Text('面对面收照片和文件', style: TextStyle(fontSize: 16)),
                  subtitle: const Text('扫二维码，给我发照片、视频和文件。',
                      style: TextStyle(fontSize: 12, color: Color(0xFF999999))),
                  trailing: const Icon(Icons.expand_more, size: 20, color: Color(0xFFC7C7C7)),
                  onTap: () => showToast(context, '活动未开放'),
                ),
              ),
            ]),
    );
  }
}
