package com.xiaoman.treehole;

/**
 * ReminderItem · 一条提醒（与 web/js/reminders.js 的 {id,text,at,done} 对齐）
 */
public final class ReminderItem {
    public final String id;
    public final String text;
    public final long at;
    public final boolean done;

    public ReminderItem(String id, String text, long at, boolean done) {
        this.id = id == null ? "" : id;
        this.text = text == null ? "" : text;
        this.at = at;
        this.done = done;
    }
}
