import 'package:flutter/material.dart';

import '../pick.dart';
import '../user_profile.dart';
import '../util.dart';
import 'my_qr_page.dart';

/// 个人资料设置页（仿微信「个人信息」布局；数据存本机）
class ProfilePage extends StatefulWidget {
  const ProfilePage({super.key});

  @override
  State<ProfilePage> createState() => _ProfilePageState();
}

class _ProfilePageState extends State<ProfilePage> {
  UserProfile? _p;

  @override
  void initState() {
    super.initState();
    UserProfile.load().then((p) {
      if (mounted) setState(() => _p = p);
    });
  }

  Future<void> _editText(String title, String value, ValueChanged<String> onSave, {int maxLength = 30}) async {
    final ctrl = TextEditingController(text: value);
    final v = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          maxLength: maxLength,
          decoration: InputDecoration(hintText: '请输入$title'),
          onSubmitted: (s) => Navigator.pop(ctx, s),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('取消', style: TextStyle(color: Colors.black))),
          TextButton(onPressed: () => Navigator.pop(ctx, ctrl.text), child: const Text('保存')),
        ],
      ),
    );
    ctrl.dispose();
    if (v == null) return;
    onSave(v.trim());
    await _p?.save();
    if (mounted) setState(() {});
  }

  Future<void> _pickAvatar() async {
    final files = await pickAssets(context, max: 1, type: RequestType.image);
    if (files.isEmpty || _p == null) return;
    await _p!.setAvatarFromFile(files.first);
    if (mounted) setState(() {});
  }

  Future<void> _pickGender() async {
    final v = await showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          for (final g in const ['男', '女', '保密'])
            ListTile(
              title: Text(g, style: const TextStyle(fontSize: 16)),
              onTap: () => Navigator.pop(ctx, g),
            ),
          const SizedBox(height: 6),
        ]),
      ),
    );
    if (v == null || _p == null) return;
    _p!.gender = v;
    await _p!.save();
    if (mounted) setState(() {});
  }

  Widget _row(String label,
      {String? value, Widget? trailing, VoidCallback? onTap, bool chevron = true}) {
    return ListTile(
      tileColor: Colors.white,
      title: Text(label, style: const TextStyle(fontSize: 16)),
      trailing: Row(mainAxisSize: MainAxisSize.min, children: [
        ?trailing,
        if (value != null)
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 190),
            child: Text(value,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.right,
                style: const TextStyle(fontSize: 16, color: Color(0xFF999999))),
          ),
        if (chevron) const Icon(Icons.chevron_right, size: 20, color: Color(0xFFC7C7C7)),
      ]),
      onTap: onTap,
    );
  }

  @override
  Widget build(BuildContext context) {
    final p = _p;
    return Scaffold(
      appBar: AppBar(title: const Text('个人资料')),
      body: p == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(children: [
              _row('头像',
                  chevron: false,
                  trailing: Padding(
                    padding: const EdgeInsets.only(top: 4, bottom: 4),
                    child: DefaultAvatar(url: p.avatar.isEmpty ? null : p.avatar, size: 52),
                  ),
                  onTap: _pickAvatar),
              _row('名字', value: p.name, onTap: () => _editText('名字', p.name, (v) => p.name = v)),
              _row('性别', value: p.gender, onTap: _pickGender),
              _row('地区', value: p.region.isEmpty ? '' : p.region,
                  onTap: () => _editText('地区', p.region, (v) => p.region = v)),
              _row('手机号', value: p.phoneMasked,
                  onTap: () => _editText('手机号', p.phone, (v) => p.phone = v, maxLength: 11)),
              _row('账号', value: p.wxid,
                  onTap: () => showToast(context, '账号 ID 是唯一凭证，不可修改')),
              _row('我的二维码',
                  trailing: const Icon(Icons.qr_code_2, size: 22, color: Color(0xFF555555)),
                  onTap: () => Navigator.push(
                      context, MaterialPageRoute(builder: (_) => const MyQrPage()))),
              _row('拍一拍',
                  value: p.pat.isEmpty ? '未设置' : p.pat,
                  onTap: () => _editText('拍一拍', p.pat, (v) => p.pat = v, maxLength: 10)),
              _row('签名',
                  value: p.signature.isEmpty ? '未填写' : p.signature,
                  onTap: () => _editText('签名', p.signature, (v) => p.signature = v, maxLength: 40)),
              const SizedBox(height: 20),
            ]),
    );
  }
}
