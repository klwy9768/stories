package com.liu.stories

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.io.FileOutputStream

class MainActivity : FlutterActivity() {
    private val channelName = "stories/native"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName).setMethodCallHandler { call, result ->
            when (call.method) {
                "videoThumb" -> {
                    val url = call.argument<String>("url") ?: ""
                    val out = call.argument<String>("out") ?: ""
                    Thread {
                        var ok = false
                        try {
                            extractVideoThumb(url, out)
                            ok = File(out).exists() && File(out).length() > 0
                        } catch (_: Exception) {
                        }
                        runOnUiThread { result.success(if (ok) out else null) }
                    }.start()
                }
                "toJpeg" -> {
                    val src = call.argument<String>("src") ?: ""
                    val out = call.argument<String>("out") ?: ""
                    val maxDim = call.argument<Int>("maxDim") ?: 2048
                    Thread {
                        var ok = false
                        try {
                            convertToJpeg(src, out, maxDim)
                            ok = File(out).exists() && File(out).length() > 0
                        } catch (_: Exception) {
                        }
                        runOnUiThread { result.success(if (ok) out else null) }
                    }.start()
                }
                "hasAllFilesAccess" -> {
                    val ok = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                        Environment.isExternalStorageManager()
                    } else {
                        true
                    }
                    result.success(ok)
                }
                "requestAllFilesAccess" -> {
                    var launched = false
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                        try {
                            val i = Intent(
                                Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                                Uri.fromParts("package", packageName, null)
                            )
                            startActivity(i)
                            launched = true
                        } catch (_: Exception) {
                            try {
                                startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
                                launched = true
                            } catch (_: Exception) {
                            }
                        }
                    }
                    result.success(launched)
                }
                "checkPermState" -> {
                    val out = org.json.JSONObject()
                    out.put("sdk", Build.VERSION.SDK_INT)
                    out.put("brand", Build.BRAND)
                    out.put("model", Build.MODEL)
                    fun chk(p: String): Boolean =
                        checkSelfPermission(p) == android.content.pm.PackageManager.PERMISSION_GRANTED
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                        out.put("read_media_images", chk("android.permission.READ_MEDIA_IMAGES"))
                        out.put("read_media_video", chk("android.permission.READ_MEDIA_VIDEO"))
                        out.put("read_media_audio", chk("android.permission.READ_MEDIA_AUDIO"))
                        out.put("visual_user_selected", chk("android.permission.READ_MEDIA_VISUAL_USER_SELECTED"))
                    } else {
                        out.put("read_external", chk("android.permission.READ_EXTERNAL_STORAGE"))
                    }
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                        out.put("all_files", Environment.isExternalStorageManager())
                    }
                    result.success(out.toString())
                }
                else -> result.notImplemented()
            }
        }
    }

    /** 视频首帧缩略图（网络 URL 直接解），宽度压到 480 内 */
    private fun extractVideoThumb(url: String, outPath: String) {
        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(url, HashMap<String, String>())
            val frame: Bitmap = retriever.getFrameAtTime(0, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                ?: retriever.frameAtTime
                ?: throw IllegalStateException("no frame")
            val bmp = if (frame.width > 480) {
                val h = (frame.height.toFloat() * 480f / frame.width).toInt().coerceAtLeast(1)
                Bitmap.createScaledBitmap(frame, 480, h, true)
            } else {
                frame
            }
            val f = File(outPath)
            f.parentFile?.mkdirs()
            FileOutputStream(f).use { os -> bmp.compress(Bitmap.CompressFormat.JPEG, 82, os) }
        } finally {
            try {
                retriever.release()
            } catch (_: Exception) {
            }
        }
    }

    /** 图片统一转 JPEG（HEIC 等 Flutter 解不了的格式靠系统解码；按 EXIF 摆正） */
    private fun convertToJpeg(srcPath: String, outPath: String, maxDim: Int) {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(srcPath, bounds)
        var sample = 1
        val maxSide = maxOf(bounds.outWidth, bounds.outHeight)
        while (maxSide / sample > maxDim) sample *= 2
        val opts = BitmapFactory.Options().apply { inSampleSize = sample }
        var bmp = BitmapFactory.decodeFile(srcPath, opts) ?: throw IllegalStateException("decode fail")
        try {
            val exif = ExifInterface(srcPath)
            val orientation =
                exif.getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
            val m = Matrix()
            when (orientation) {
                ExifInterface.ORIENTATION_ROTATE_90 -> m.postRotate(90f)
                ExifInterface.ORIENTATION_ROTATE_180 -> m.postRotate(180f)
                ExifInterface.ORIENTATION_ROTATE_270 -> m.postRotate(270f)
                ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> m.postScale(-1f, 1f)
                ExifInterface.ORIENTATION_FLIP_VERTICAL -> m.postScale(1f, -1f)
            }
            if (!m.isIdentity) {
                bmp = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
            }
        } catch (_: Exception) {
        }
        val f = File(outPath)
        f.parentFile?.mkdirs()
        FileOutputStream(f).use { os -> bmp.compress(Bitmap.CompressFormat.JPEG, 90, os) }
    }
}
