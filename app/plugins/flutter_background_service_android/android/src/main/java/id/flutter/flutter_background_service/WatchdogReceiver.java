package id.flutter.flutter_background_service;

import static android.os.Build.VERSION.SDK_INT;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Stories 补丁：禁用看门狗自动重启。
 * 原因：原实现的 enqueue 会在服务每次销毁后安排 5 秒重启闹钟，
 * 在 Android 14+/16 上反复尝试启动受限的 dataSync 前台服务，导致整个 App 进程随机崩溃。
 * 本应用改为：服务被系统杀掉后，等使用者再次打开 App 时由前台逻辑重新启动。
 */
public class WatchdogReceiver extends BroadcastReceiver {
    private static final int QUEUE_REQUEST_ID = 111;
    private static final String ACTION_RESPAWN = "id.flutter.background_service.RESPAWN";

    public static void enqueue(Context context) {
        // Stories patch: disabled（不再安排重启闹钟）
    }

    public static void enqueue(Context context, int millis) {
        // Stories patch: disabled（不再安排重启闹钟）
    }

    public static void remove(Context context) {
        try {
            Intent intent = new Intent(context, WatchdogReceiver.class);
            intent.setAction(ACTION_RESPAWN);

            int flags = PendingIntent.FLAG_CANCEL_CURRENT;
            if (SDK_INT >= android.os.Build.VERSION_CODES.S) {
                flags |= PendingIntent.FLAG_MUTABLE;
            }

            PendingIntent pi = PendingIntent.getBroadcast(context, WatchdogReceiver.QUEUE_REQUEST_ID, intent, flags);
            AlarmManager alarmManager = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
            if (alarmManager != null) {
                alarmManager.cancel(pi);
            }
        } catch (Exception ignored) {
        }
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        // Stories patch: 不再自动拉起服务，避免随机崩溃
    }
}
