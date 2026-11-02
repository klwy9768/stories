// Copyright (c) 2026 刘晨宇 <https://github.com/klwy9768/stories>
// SPDX-License-Identifier: MIT

import 'local_db.dart';

/// 演示模式：构建时加 --dart-define=DEMO=true 生效。
/// 首次启动往本地库写入几段示例对话，不连接任何服务器，
/// 仅用于让人快速看到界面长什么样。
const bool kDemoMode = bool.fromEnvironment('DEMO');

class DemoData {
  static Future<void> seedIfEmpty() async {
    if (!kDemoMode) return;
    await LocalDb.init();
    final existing = await LocalDb.allChars();
    if (existing.isNotEmpty) return;

    final now = DateTime.now().millisecondsSinceEpoch / 1000.0;
    for (final s in _samples(now)) {
      final c = s['char'] as Map<String, dynamic>;
      await LocalDb.upsertChar(c);
      final msgs = (s['msgs'] as List).cast<Map<String, dynamic>>();
      await LocalDb.insertMessages(c['id'] as int, msgs);
    }
  }

  static Map<String, dynamic> _chat() => {
        'photo_enabled': true,
        'voice_enabled': true,
        'sticker_enabled': true,
        'video_enabled': false,
        'img_desc_enabled': true,
        'voice_desc_enabled': true,
        'wake_enabled': true,
        'reminder_enabled': 0,
        'reminder_target': 5,
        'moments_auto': 0,
        'moments_react': 1,
        'background': '',
      };

  static List<Map<String, dynamic>> _samples(double now) => [
        {
          'char': {
            'id': 9001,
            'name': '林晚',
            'avatar': '',
            'description': '住在隔壁的学妹，话不多但很会照顾人',
            'greeting': '嗨，今天也辛苦啦～',
            'enabled': 1,
            'chat': _chat(),
            'last': {'content': '那说好了，明天见。', 'ts': now - 120},
          },
          'msgs': [
            {'id': 1, 'role': 'character', 'kind': 'text', 'content': '嗨，今天也辛苦啦～', 'ts': now - 3600},
            {'id': 2, 'role': 'user', 'kind': 'text', 'content': '刚下课，有点累', 'ts': now - 3500},
            {'id': 3, 'role': 'character', 'kind': 'text', 'content': '那先别想作业了，去吃点东西吧。', 'ts': now - 3480},
            {'id': 4, 'role': 'user', 'kind': 'text', 'content': '你吃了吗', 'ts': now - 3400},
            {'id': 5, 'role': 'character', 'kind': 'text', 'content': '吃了呀，食堂今天的番茄牛腩还不错。', 'ts': now - 3380},
            {'id': 6, 'role': 'character', 'kind': 'text', 'content': '下次带你去，不过你得早点起。', 'ts': now - 3360},
            {'id': 7, 'role': 'user', 'kind': 'text', 'content': '行，明天叫你', 'ts': now - 200},
            {'id': 8, 'role': 'character', 'kind': 'text', 'content': '那说好了，明天见。', 'ts': now - 120},
          ],
        },
        {
          'char': {
            'id': 9002,
            'name': '苏念',
            'avatar': '',
            'description': '图书馆常驻，嘴上冷淡，回复从不敷衍',
            'greeting': '又来自习？座位给你留着。',
            'enabled': 1,
            'chat': _chat(),
            'last': {'content': '记得带伞。', 'ts': now - 900},
          },
          'msgs': [
            {'id': 1, 'role': 'character', 'kind': 'text', 'content': '又来自习？座位给你留着。', 'ts': now - 7200},
            {'id': 2, 'role': 'user', 'kind': 'text', 'content': '今天可能晚点到', 'ts': now - 7100},
            {'id': 3, 'role': 'character', 'kind': 'text', 'content': '嗯，靠窗那个位置。', 'ts': now - 7080},
            {'id': 4, 'role': 'user', 'kind': 'text', 'content': '你怎么总坐那儿', 'ts': now - 7000},
            {'id': 5, 'role': 'character', 'kind': 'text', 'content': '光线好，而且能看到门口。', 'ts': now - 6980},
            {'id': 6, 'role': 'character', 'kind': 'text', 'content': '……别多想，只是习惯。', 'ts': now - 6960},
            {'id': 7, 'role': 'character', 'kind': 'text', 'content': '记得带伞。', 'ts': now - 900},
          ],
        },
        {
          'char': {
            'id': 9003,
            'name': '阿梨',
            'avatar': '',
            'description': '同桌，永远在分享零食和八卦',
            'greeting': '救命，今天老师点名了！',
            'enabled': 1,
            'chat': _chat(),
            'last': {'content': '给你留了半块蛋糕，速来。', 'ts': now - 60},
          },
          'msgs': [
            {'id': 1, 'role': 'character', 'kind': 'text', 'content': '救命，今天老师点名了！', 'ts': now - 5400},
            {'id': 2, 'role': 'user', 'kind': 'text', 'content': '啊？我是不是被记了', 'ts': now - 5300},
            {'id': 3, 'role': 'character', 'kind': 'text', 'content': '没有，我帮你答到了，惊不惊喜', 'ts': now - 5280},
            {'id': 4, 'role': 'user', 'kind': 'text', 'content': '你可真是我亲同桌', 'ts': now - 5200},
            {'id': 5, 'role': 'character', 'kind': 'text', 'content': '那当然，不过下次奶茶你请。', 'ts': now - 5180},
            {'id': 6, 'role': 'character', 'kind': 'text', 'content': '给你留了半块蛋糕，速来。', 'ts': now - 60},
          ],
        },
      ];
}
