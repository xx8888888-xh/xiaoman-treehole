# ProGuard rules for 小满树洞
# Keep WebView JavaScript interfaces
# 注意：AndroidTTS 桥现在由 MainActivity.Bridge（继承 AndroidTtsBridge）与
# FloatWindowService 里的 AndroidTtsBridge 两处挂载，@JavascriptInterface 方法
# 可能声明在两个类上，所以按"包内所有 @JavascriptInterface 方法"统一保活。
-keepclassmembers class com.xiaoman.treehole.** {
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