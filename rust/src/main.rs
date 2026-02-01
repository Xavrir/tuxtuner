mod system_info;
mod ui;

use gtk4::prelude::*;
use libadwaita as adw;

const APP_ID: &str = "com.github.xavrir.TuxTuner";

fn main() -> gtk4::glib::ExitCode {
    let app = adw::Application::builder()
        .application_id(APP_ID)
        .build();

    app.connect_startup(|app| {
        ui::load_css();
        ui::setup_actions(app);
    });

    app.connect_activate(|app| {
        let window = ui::TuxTunerWindow::new(app);
        window.present();
    });

    app.run()
}
