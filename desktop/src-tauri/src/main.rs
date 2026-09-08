use tauri::{WebviewUrl, WebviewWindowBuilder};

const FUSION_URL: &str = "http://127.0.0.1:8010";

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let url = FUSION_URL.parse().expect("Fusion loopback URL must be valid");
            let window = WebviewWindowBuilder::new(app, "panda-fusion", WebviewUrl::External(url))
                .title("Panda Fusión")
                .inner_size(1440.0, 940.0)
                .min_inner_size(1100.0, 720.0)
                .build()?;
            window.show()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("failed to run Panda Fusión desktop");
}
