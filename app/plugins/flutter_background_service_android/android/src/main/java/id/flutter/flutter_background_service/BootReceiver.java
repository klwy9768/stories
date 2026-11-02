package id.flutter.flutter_background_service;

import android.annotation.SuppressLint;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Stories 补丁：完全禁用「开机/应用更新自启」。
 * 原因：autoStartOnBoot 默认 true 会在每次 APK 安装（MY_PACKAGE_REPLACED）与开机时强启前台服务，
 * 在 Android 14+/16 上会被系统拒绝并导致进程崩溃；且会点燃 Watchdog 无限重启循环。
 * 本应用只在使用者主动开启「后台通知」时由 App 前台登记启动服务，不需要任何自启。
 */
public class BootReceiver extends BroadcastReceiver {
    @SuppressLint("WakelockTimeout")
    @Override
    public void onReceive(Context context, Intent intent) {
        // Stories patch: disabled
    }
}
