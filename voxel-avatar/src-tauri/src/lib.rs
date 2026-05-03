#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  // 强制 WebView2 使用 GPU 硬件加速（仅 Windows）
  #[cfg(target_os = "windows")]
  std::env::set_var("WEBVIEW2_ADDITIONAL_BROWSER_ARGS",
    "--enable-gpu --enable-gpu-rasterization --enable-zero-copy \
     --ignore-gpu-blocklist --enable-hardware-overlays");

  tauri::Builder::default()
    .setup(|app| {
      // macOS：保持原生窗口与 WKWebView 都使用透明底色
      #[cfg(target_os = "macos")]
      {
        use tauri::Manager;
        if let Some(win) = app.get_webview_window("main") {
          let _ = win.with_webview(|webview| unsafe {
            use objc2_app_kit::{NSColor, NSWindow};
            use objc2_web_kit::WKWebView;

            let clear = NSColor::clearColor();
            let window: &NSWindow = &*webview.ns_window().cast();
            let view: &WKWebView = &*webview.inner().cast();

            window.setOpaque(false);
            window.setBackgroundColor(Some(&clear));
            window.setHasShadow(false);

            // 处理 macOS 12+ 的 page overscroll 区域，避免边缘露白。
            view.setUnderPageBackgroundColor(Some(&clear));
          });
        }
      }

      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }
      Ok(())
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
