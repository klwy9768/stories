import 'dart:io';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:wechat_assets_picker/wechat_assets_picker.dart';
import 'package:wechat_camera_picker/wechat_camera_picker.dart';

import 'log.dart';
import 'util.dart';

export 'package:wechat_assets_picker/wechat_assets_picker.dart' show RequestType;

/// 相册权限检查结果
enum GalleryPermResult { ok, useSystem, denied }

/// 相册权限预检：授权（含"仅选择的照片"）直接放行；
/// 被拒绝时弹准确路径引导，并给出"改用系统相册"兜底（系统相册无需权限）。
Future<GalleryPermResult> checkGalleryPermission(
  BuildContext context, {
  RequestType type = RequestType.common,
}) async {
  PermissionState ps;
  try {
    ps = await PhotoManager.requestPermissionExtend(
      requestOption: PermissionRequestOption(
        androidPermission: AndroidPermission(type: type, mediaLocation: false),
      ),
    );
  } catch (e) {
    Log.error('perm', 'permission check error: $e');
    return GalleryPermResult.ok; // 查询异常不拦截，交给选择器自行处理
  }
  if (ps.hasAccess) {  // 完整授权或"仅选择的照片"（limited）都可用
    if (ps.isLimited && context.mounted) {
      showToast(context, '当前仅可访问部分照片；如需全部，请在系统设置把「照片和视频」改为「允许」');
    }
    return GalleryPermResult.ok;
  }
  // 上报手机侧真实权限状态（便于远程排查）
  try {
    final st = await nativePermState();
    Log.error('perm', 'gallery denied: state=$ps native=$st');
  } catch (_) {}
  if (!context.mounted) return GalleryPermResult.denied;
  final choice = await showDialog<String>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('无法访问相册'),
      content: const Text('系统里已开启「照片和视频」仍无法访问时，可先改用系统相册（无需权限）。\n\n'
          '开启相册权限的路径：\n'
          '设置 → 应用设置 → 应用管理 → Stories → 权限管理 → 照片和视频 → 选择「允许」'),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx, 'system'), child: const Text('用系统相册')),
        TextButton(onPressed: () => Navigator.pop(ctx, 'settings'), child: const Text('去设置')),
      ],
    ),
  );
  if (choice == 'settings') {
    try {
      await PhotoManager.openSetting();
    } catch (_) {}
  }
  if (choice == 'system') return GalleryPermResult.useSystem;
  return GalleryPermResult.denied;
}

/// 系统相册（Android 系统照片选择器，无需任何权限）
Future<List<File>> _systemPickImages(int max) async {
  try {
    final xs = await ImagePicker().pickMultiImage(limit: max < 2 ? 2 : max);
    return [for (final x in xs.take(max)) File(x.path)];
  } catch (e) {
    Log.error('perm', 'system picker failed: $e');
    return [];
  }
}

/// 常见视频扩展名判定
bool isVideoPath(String path) {
  final lower = path.toLowerCase();
  return ['.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v']
      .any(lower.endsWith);
}

/// 微信风格相册选择器，返回所选文件（最多 [max] 个）
Future<List<File>> pickAssets(
  BuildContext context, {
  int max = 1,
  RequestType type = RequestType.common,
}) async {
  if (!context.mounted) return [];
  final perm = await checkGalleryPermission(context, type: type);
  if (perm == GalleryPermResult.useSystem) {
    if (!type.containsImage()) return [];
    final xs = await _systemPickImages(max);
    final withJpeg = <File>[];
    for (final f in xs) {
      withJpeg.add(await ensureJpeg(f));
    }
    return withJpeg;
  }
  if (perm != GalleryPermResult.ok) return [];
  if (!context.mounted) return [];
  final assets = await AssetPicker.pickAssets(
    context,
    pickerConfig: AssetPickerConfig(
      maxAssets: max,
      requestType: type,
      themeColor: kWeChatTheme,
    ),
  );
  if (assets == null) return [];
  final out = <File>[];
  for (final a in assets) {
    try {
      final f = await a.file ?? await a.originFile;
      if (f != null) out.add(f);
    } catch (_) {}
  }
  return out;
}

/// 微信风格相机（拍照/录像二合一），返回拍摄的文件
Future<File?> pickCamera(BuildContext context, {bool videoOnly = false}) async {
  final a = await CameraPicker.pickFromCamera(
    context,
    pickerConfig: CameraPickerConfig(
      enableRecording: true,
      enableAudio: true,
      onlyEnableRecording: videoOnly,
    ),
  );
  if (a == null) return null;
  try {
    return await a.file ?? await a.originFile;
  } catch (_) {
    return null;
  }
}

/// 把图片规整为 JPEG（HEIC 等 Flutter 解不了的格式用系统解码转换；动图/失败退回原文件）
Future<File> ensureJpeg(File f) async {
  final lower = f.path.toLowerCase();
  if (lower.endsWith('.gif')) return f;
  final j = await nativeToJpeg(f);
  return j ?? f;
}

/// 微信风格相册（只选图片），并统一转成 JPEG 后返回
Future<List<File>> pickImagesJpeg(BuildContext context, {int max = 9}) async {
  final files = await pickAssets(context, max: max, type: RequestType.image);
  final out = <File>[];
  for (final f in files) {
    out.add(await ensureJpeg(f));
  }
  return out;
}
