import 'package:flutter/material.dart';

/// 开源许可声明页：列出本应用使用的开源组件及其许可证
class OpenSourceLicensePage extends StatelessWidget {
  const OpenSourceLicensePage({super.key});

  static const List<(String, String, String)> _items = [
    ('Flutter / Dart', 'BSD-3-Clause', 'https://github.com/flutter/flutter'),
    ('wechat_assets_picker', 'Apache-2.0', 'https://github.com/fluttercandies/flutter_wechat_assets_picker'),
    ('wechat_camera_picker', 'Apache-2.0', 'https://github.com/fluttercandies/flutter_wechat_camera_picker'),
    ('photo_manager', 'Apache-2.0', 'https://github.com/fluttercandies/flutter_photo_manager'),
    ('video_player', 'BSD-3-Clause', 'https://github.com/flutter/packages'),
    ('audioplayers', 'MIT', 'https://github.com/bluefireteam/audioplayers'),
    ('shared_preferences', 'BSD-3-Clause', 'https://github.com/flutter/packages'),
    ('flutter_local_notifications', 'BSD-3-Clause', 'https://github.com/MaikuB/flutter_local_notifications'),
    ('flutter_background_service', 'MIT', 'https://github.com/ekasetiawans/flutter_background_service'),
    ('open_filex', 'MIT', 'https://github.com/crazecoder/open_file'),
    ('file_picker', 'MIT', 'https://github.com/miguelpruivo/flutter_file_picker'),
    ('http', 'BSD-3-Clause', 'https://github.com/dart-lang/http'),
    ('path_provider', 'BSD-3-Clause', 'https://github.com/flutter/packages'),
    ('image_picker', 'Apache-2.0', 'https://github.com/flutter/packages'),
    ('qr_flutter', 'BSD-3-Clause', 'https://github.com/theyakka/qr.flutter'),
    ('qr (Dart)', 'Apache-2.0', 'https://github.com/kevmoo/qr.dart'),
    ('record', 'BSD-3-Clause', 'https://github.com/llfbandit/record'),
    ('Material Icons', 'Apache-2.0', 'https://fonts.google.com/icons'),
  ];

  static void _show(BuildContext context, (String, String, String) it) {
    showModalBottomSheet<void>(
      context: context,
      builder: (_) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 20, 20, 30),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(it.$1, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text('许可证：${it.$2}', style: const TextStyle(fontSize: 13, color: Color(0xFF666666))),
          const SizedBox(height: 8),
          const Text('项目主页（可长按复制）：', style: TextStyle(fontSize: 12, color: Color(0xFF999999))),
          const SizedBox(height: 4),
          SelectableText(it.$3, style: const TextStyle(fontSize: 12, color: Color(0xFF576B95))),
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('开源许可')),
      body: ListView(
        children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 14, 16, 6),
            child: Text(
              '本应用在开发中使用了以下开源组件（均为公开授权），在此致谢。\n'
              '应用内所有图标与插图均为原创绘制或来自上述开源组件，未使用任何第三方产品的专有素材。',
              style: TextStyle(fontSize: 13, color: Color(0xFF888888), height: 1.5),
            ),
          ),
          Container(
            color: Colors.white,
            child: Column(
              children: [
                for (final it in _items)
                  ListTile(
                    dense: true,
                    title: Text(it.$1, style: const TextStyle(fontSize: 15)),
                    subtitle: Text(it.$2, style: const TextStyle(fontSize: 12, color: Color(0xFF888888))),
                    trailing: const Icon(Icons.info_outline, size: 18, color: Color(0xFFC7C7C7)),
                    onTap: () => _show(context, it),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 20),
        ],
      ),
    );
  }
}
