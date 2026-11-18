use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    process::Command,
    thread,
    time::Duration,
};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

#[cfg(target_os = "linux")]
use webkit2gtk::{
    glib::prelude::*, PermissionRequestExt, SettingsExt, WebViewExt, UserMediaPermissionRequestExt,
};

fn fusion_address() -> String {
    let port = std::env::var("FUSION_READER_V2_PORT").ok()
        .and_then(|value| value.parse::<u16>().ok()).filter(|port| *port > 0).unwrap_or(8010);
    format!("127.0.0.1:{port}")
}
fn fusion_url() -> String { format!("http://{}", fusion_address()) }

fn fusion_is_ready() -> bool {
    let address: SocketAddr = match fusion_address().parse() {
        Ok(address) => address,
        Err(_) => return false,
    };
    let mut stream = match TcpStream::connect_timeout(&address, Duration::from_millis(400)) {
        Ok(stream) => stream,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(900)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(900)));
    if stream
        .write_all(b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok()
        && (response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200"))
}

fn start_fusion_if_needed() {
    if !fusion_is_ready() {
        let _ = Command::new("fusionctl").arg("start").spawn();
    }
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            WebviewWindowBuilder::new(
                app,
                "panda-fusion-loading",
                WebviewUrl::App("desktop-loading.html".into()),
            )
                .title("Panda Fusión")
                .inner_size(1440.0, 940.0)
                .min_inner_size(1100.0, 720.0)
                .build()?;

            let handle = app.handle().clone();
            thread::spawn(move || {
                start_fusion_if_needed();
                for _ in 0..150 {
                    if fusion_is_ready() {
                        let app_handle = handle.clone();
                        let _ = handle.run_on_main_thread(move || {
                            let url = fusion_url().parse().expect("Fusion loopback URL must be valid");
                            if WebviewWindowBuilder::new(
                                &app_handle,
                                "panda-fusion",
                                WebviewUrl::External(url),
                            )
                            .title("Panda Fusión")
                            .inner_size(1440.0, 940.0)
                            .min_inner_size(1100.0, 720.0)
                            .build()
                            .map(|reader| {
                                // WebKitGTK denies getUserMedia requests by default when no
                                // permission-request handler is connected.  The reader is a
                                // fixed loopback window; grant only its microphone request.
                                #[cfg(target_os = "linux")]
                                let _ = reader.with_webview(|platform_webview| {
                                    let native_webview = platform_webview.inner();
                                    if let Some(settings) = native_webview.settings() {
                                        settings.set_enable_media_stream(true);
                                        settings.set_enable_webrtc(true);
                                    }
                                    native_webview.connect_permission_request(|view, request| {
                                        if let Some(media) = request.downcast_ref::<webkit2gtk::UserMediaPermissionRequest>() {
                                            let expected = fusion_url();
                                            let trusted = view.uri().and_then(|uri| tauri::Url::parse(uri.as_str()).ok())
                                                .map(|uri| uri.origin().ascii_serialization() == expected).unwrap_or(false);
                                            if trusted && media.is_for_audio_device() && !media.is_for_video_device() {
                                                request.allow();
                                            } else {
                                                request.deny();
                                            }
                                            true
                                        } else { false }
                                    });
                                });
                            })
                            .is_ok()
                            {
                                if let Some(loading) = app_handle.get_webview_window("panda-fusion-loading") {
                                    let _ = loading.close();
                                }
                            }
                        });
                        return;
                    }
                    thread::sleep(Duration::from_millis(400));
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("failed to run Panda Fusión desktop");
}
