# ProGuard rules for 小满树洞
# Keep WebView JavaScript interfaces
-keepclassmembers class com.xiaoman.treehole.MainActivity$Bridge {
    @android.webkit.JavascriptInterface <methods>;
}

# Keep WebView and related classes
-keep class android.webkit.** { *; }

# Keep TTS classes
-keep class android.speech.tts.** { *; }

# Keep annotations
-keepattributes *Annotation*,JavascriptInterface,Signature

# Keep enums
-keepclassmembers enum * {
    public static **[] values();
    public static ** valueOf(java.lang.String);
}