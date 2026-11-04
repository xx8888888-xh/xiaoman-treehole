package com.xiaoman.treehole;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * ReminderStore · 提醒的原生镜像存储
 * ------------------------------------------------------------
 * WebView 的 localStorage 在 Java 侧读不到，所以 JS 每次增删改提醒后经
 * AndroidHost.syncReminders(json) 推一份整表过来，这里落 SharedPreferences。
 * 锁屏/进程被杀后 AlarmManager 只认这份镜像，因此它是"提醒能在后台响"的根。
 */
public final class ReminderStore {

    private static final String TAG = "ReminderStore";
    static final String PREFS = "treehole_reminder_store";
    private static final String KEY_ITEMS = "items";
    private static final String KEY_DELIVERED = "delivered";

    private ReminderStore() {}

    private static SharedPreferences prefs(Context ctx) {
        return ctx.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** 整表覆盖（JS 是唯一写入方，原生只读；避免两边各写一半造成漂移） */
    public static void save(Context ctx, String json) {
        if (json == null) return;
        try {
            JSONArray src = new JSONArray(json);
            JSONArray out = new JSONArray();
            for (int i = 0; i < src.length(); i++) {
                JSONObject o = src.optJSONObject(i);
                if (o == null) continue;
                JSONObject n = new JSONObject();
                n.put("id", o.optString("id", ""));
                n.put("text", o.optString("text", ""));
                n.put("at", o.optLong("at", 0L));
                n.put("done", o.optBoolean("done", false));
                if (n.optString("id").isEmpty()) continue;
                out.put(n);
            }
            prefs(ctx).edit().putString(KEY_ITEMS, out.toString()).apply();
        } catch (Exception e) {
            Log.w(TAG, "save failed: " + e.getMessage());
        }
    }

    public static List<ReminderItem> all(Context ctx) {
        List<ReminderItem> list = new ArrayList<>();
        try {
            JSONArray arr = new JSONArray(prefs(ctx).getString(KEY_ITEMS, "[]"));
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.optJSONObject(i);
                if (o == null) continue;
                list.add(new ReminderItem(o.optString("id"), o.optString("text"),
                        o.optLong("at", 0L), o.optBoolean("done", false)));
            }
        } catch (Exception e) {
            Log.w(TAG, "load failed: " + e.getMessage());
        }
        return list;
    }

    public static long[] pendingAts(Context ctx) {
        List<ReminderItem> list = all(ctx);
        List<Long> ats = new ArrayList<>();
        for (ReminderItem r : list) {
            if (!r.done) ats.add(r.at);
        }
        long[] out = new long[ats.size()];
        for (int i = 0; i < out.length; i++) out[i] = ats.get(i);
        return out;
    }

    /** 到期待投递的提醒（未完成且未被原生投递过） */
    public static List<ReminderItem> due(Context ctx, long nowMs) {
        List<ReminderItem> list = all(ctx);
        List<ReminderItem> out = new ArrayList<>();
        for (ReminderItem r : list) {
            if (!r.done && ReminderScheduling.isDue(r.at, nowMs) && !isDelivered(ctx, r.id)) {
                out.add(r);
            }
        }
        return out;
    }

    public static boolean isDelivered(Context ctx, String id) {
        return deliveredIds(ctx).contains(id);
    }

    private static List<String> deliveredIds(Context ctx) {
        List<String> out = new ArrayList<>();
        try {
            JSONArray arr = new JSONArray(prefs(ctx).getString(KEY_DELIVERED, "[]"));
            for (int i = 0; i < arr.length(); i++) out.add(arr.optString(i, ""));
        } catch (Exception ignored) {
        }
        return out;
    }

    /** 原生投递成功 → 记录"已送达"，防止同一条重复弹通知 */
    public static void markDelivered(Context ctx, String id) {
        if (id == null || id.isEmpty()) return;
        List<String> ids = deliveredIds(ctx);
        if (!ids.contains(id)) ids.add(id);
        prefs(ctx).edit().putString(KEY_DELIVERED, new JSONArray(ids).toString()).apply();
    }

    /**
     * JS 打开时取走"原生已投递"的 id（取走即清空）：
     * web 侧据此把这些提醒标成完成，避免同一件事既弹过通知又在聊天里再催一遍。
     */
    public static String drainDelivered(Context ctx) {
        List<String> ids = deliveredIds(ctx);
        prefs(ctx).edit().putString(KEY_DELIVERED, "[]").apply();
        return new JSONArray(ids).toString();
    }

    /** 通知里的"稍后提醒"：把该条推迟，并允许再次投递 */
    public static void snooze(Context ctx, String id, long delayMs) {
        List<ReminderItem> list = all(ctx);
        JSONArray out = new JSONArray();
        long at = System.currentTimeMillis() + Math.max(60_000L, delayMs);
        for (ReminderItem r : list) {
            JSONObject o = new JSONObject();
            try {
                o.put("id", r.id);
                o.put("text", r.text);
                o.put("at", id != null && id.equals(r.id) ? at : r.at);
                o.put("done", r.done);
            } catch (Exception ignored) { }
            out.put(o);
        }
        prefs(ctx).edit().putString(KEY_ITEMS, out.toString()).apply();
        List<String> ids = deliveredIds(ctx);
        ids.remove(id);
        prefs(ctx).edit().putString(KEY_DELIVERED, new JSONArray(ids).toString()).apply();
    }
}
