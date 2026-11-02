import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 个人资料（单用户应用：存本机 SharedPreferences，不上传服务器）
class UserProfile {
  String name;
  String gender;
  String region;
  String phone;
  String wxid;
  String signature;
  String pat;
  String avatar; // 本地文件路径（file://...）或空

  UserProfile({
    this.name = '用户',
    this.gender = '男',
    this.region = '',
    this.phone = '',
    this.wxid = '',
    this.signature = '',
    this.pat = '',
    this.avatar = '',
  });

  static const _key = 'user_profile';
  static UserProfile? _cached;

  /// 手机号打码显示（180******53）
  String get phoneMasked {
    final p = phone.trim();
    if (p.length >= 7) return '${p.substring(0, 3)}******${p.substring(p.length - 2)}';
    return p;
  }

  static Future<UserProfile> load() async {
    final cached = _cached;
    if (cached != null) return cached;
    final p = UserProfile();
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_key);
      if (raw != null && raw.isNotEmpty) {
        final m = (jsonDecode(raw) as Map).cast<String, dynamic>();
        p.name = (m['name'] ?? p.name) as String;
        p.gender = (m['gender'] ?? p.gender) as String;
        p.region = (m['region'] ?? p.region) as String;
        p.phone = (m['phone'] ?? p.phone) as String;
        p.wxid = (m['wxid'] ?? p.wxid) as String;
        p.signature = (m['signature'] ?? p.signature) as String;
        p.pat = (m['pat'] ?? p.pat) as String;
        p.avatar = (m['avatar'] ?? p.avatar) as String;
      }
      if (p.wxid.isEmpty) {
        p.wxid = 'stories_${_randId()}';
        await p.save();
      }
    } catch (_) {}
    _cached = p;
    return p;
  }

  static String _randId() {
    const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
    final r = Random();
    return List.generate(12, (_) => chars[r.nextInt(chars.length)]).join();
  }

  Future<void> save() async {
    _cached = this;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(
          _key,
          jsonEncode({
            'name': name,
            'gender': gender,
            'region': region,
            'phone': phone,
            'wxid': wxid,
            'signature': signature,
            'pat': pat,
            'avatar': avatar,
          }));
    } catch (_) {}
  }

  /// 把选中的头像图片复制到应用目录，返回 file:// 路径
  Future<String> setAvatarFromFile(File src) async {
    final dir = await getApplicationDocumentsDirectory();
    final dot = src.path.lastIndexOf('.');
    final ext = dot >= 0 ? src.path.substring(dot) : '.jpg';
    final dst = File('${dir.path}/profile_avatar$ext');
    // 清掉旧头像文件（扩展名可能不同，避免留下孤儿文件）
    try {
      await for (final f in dir.list()) {
        final n = f.path.split(Platform.pathSeparator).last;
        if (n.startsWith('profile_avatar') && n != 'profile_avatar$ext') {
          try {
            await f.delete();
          } catch (_) {}
        }
      }
    } catch (_) {}
    if (await dst.exists()) {
      await dst.delete();
    }
    await src.copy(dst.path);
    avatar = 'file://${dst.path}';
    await save();
    return avatar;
  }
}
