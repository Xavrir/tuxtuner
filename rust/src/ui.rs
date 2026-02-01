use crate::system_info::{self, SystemInfo, VALID_GPU_MODES};
use gtk4::glib::{self, clone};
use gtk4::prelude::*;
use gtk4::{gio, Align, Box as GtkBox, Button, CssProvider, Label, Orientation, PolicyType, ScrolledWindow, StringList};
use libadwaita as adw;
use adw::prelude::*;
use std::cell::{Cell, RefCell};
use std::rc::Rc;

const APP_CSS: &str = r#"
.tuxtuner-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    border-radius: 0 0 20px 20px;
    padding: 28px 20px 24px 20px;
}

.tuxtuner-title {
    font-size: 28px;
    font-weight: 800;
    letter-spacing: 3px;
    color: #e94560;
    text-shadow: 0 2px 12px rgba(233, 69, 96, 0.5);
}

.tuxtuner-subtitle {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 2px;
    color: rgba(255, 255, 255, 0.6);
    margin-top: 2px;
}

.tuxtuner-penguin {
    font-size: 36px;
    margin-right: 14px;
}

.status-value {
    font-size: 14px;
    font-weight: 700;
    color: #e94560;
}

.status-value-green {
    font-size: 14px;
    font-weight: 700;
    color: #4ade80;
}

.native-badge {
    background: linear-gradient(135deg, #e94560 0%, #ff6b9d 100%);
    color: white;
    font-size: 9px;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 6px;
    margin-left: 6px;
}
"#;

pub fn load_css() {
    let provider = CssProvider::new();
    provider.load_from_string(APP_CSS);

    gtk4::style_context_add_provider_for_display(
        &gtk4::gdk::Display::default().expect("Could not get default display"),
        &provider,
        gtk4::STYLE_PROVIDER_PRIORITY_APPLICATION,
    );
}

pub fn setup_actions(_app: &adw::Application) {}

pub struct TuxTunerWindow {
    window: adw::ApplicationWindow,
    toast_overlay: adw::ToastOverlay,
    banner: adw::Banner,
    status_mode_val: Label,
    status_cpu_val: Label,
    status_hz_val: Label,
    native_badge: Label,
    cpu_spin: adw::SpinRow,
    cpu_apply_btn: Button,
    gpu_combo: adw::ComboRow,
    hz_combo: adw::ComboRow,
    state: Rc<RefCell<WindowState>>,
    updating_ui: Rc<Cell<bool>>,
}

#[derive(Default)]
struct WindowState {
    max_cpu_threads: u32,
    current_cpu_threads: u32,
    current_gpu_mode: String,
    pending_gpu_mode: String,
    gpu_modes: Vec<String>,
    available_refresh_rates: Vec<String>,
    current_refresh_rate: String,
    native_refresh_rate: String,
    monitor_name: String,
}

impl TuxTunerWindow {
    pub fn new(app: &adw::Application) -> adw::ApplicationWindow {
        let main_content = GtkBox::new(Orientation::Vertical, 0);
        let toast_overlay = adw::ToastOverlay::new();
        toast_overlay.set_child(Some(&main_content));

        let window = adw::ApplicationWindow::builder()
            .application(app)
            .title("TuxTuner")
            .default_width(460)
            .default_height(680)
            .content(&toast_overlay)
            .build();

        let header_box = Self::build_header();
        main_content.append(&header_box);

        let banner = adw::Banner::new("Graphics mode change requires logout.");
        banner.set_button_label(Some("Switch & Log Out"));
        main_content.append(&banner);

        let scroll = ScrolledWindow::builder()
            .vexpand(true)
            .hscrollbar_policy(PolicyType::Never)
            .build();
        main_content.append(&scroll);

        let content_box = GtkBox::builder()
            .orientation(Orientation::Vertical)
            .spacing(0)
            .margin_top(8)
            .margin_bottom(24)
            .build();
        scroll.set_child(Some(&content_box));

        let page = adw::PreferencesPage::new();
        content_box.append(&page);

        let (status_group, status_mode_val, status_cpu_val, status_hz_val, native_badge) =
            Self::build_status_group();
        page.add(&status_group);

        let (cpu_group, cpu_spin, cpu_apply_btn) = Self::build_cpu_group();
        page.add(&cpu_group);

        let (gpu_group, gpu_combo) = Self::build_gpu_group();
        page.add(&gpu_group);

        let (display_group, hz_combo) = Self::build_display_group();
        page.add(&display_group);

        let state = Rc::new(RefCell::new(WindowState::default()));
        let updating_ui = Rc::new(Cell::new(false));

        let win = Self {
            window: window.clone(),
            toast_overlay,
            banner,
            status_mode_val,
            status_cpu_val,
            status_hz_val,
            native_badge,
            cpu_spin,
            cpu_apply_btn,
            gpu_combo,
            hz_combo,
            state,
            updating_ui,
        };

        win.setup_signals();
        win.load_data();

        window
    }

    fn build_header() -> GtkBox {
        let header_box = GtkBox::builder()
            .orientation(Orientation::Horizontal)
            .css_classes(["tuxtuner-header"])
            .halign(Align::Fill)
            .build();

        let penguin_label = Label::builder()
            .label("🐧")
            .css_classes(["tuxtuner-penguin"])
            .build();
        header_box.append(&penguin_label);

        let title_box = GtkBox::builder()
            .orientation(Orientation::Vertical)
            .valign(Align::Center)
            .build();

        let title_label = Label::builder()
            .label("TUXTUNER")
            .css_classes(["tuxtuner-title"])
            .halign(Align::Start)
            .build();
        title_box.append(&title_label);

        let subtitle_label = Label::builder()
            .label("SYSTEM PERFORMANCE CONTROL")
            .css_classes(["tuxtuner-subtitle"])
            .halign(Align::Start)
            .build();
        title_box.append(&subtitle_label);

        header_box.append(&title_box);
        header_box
    }

    fn build_status_group() -> (adw::PreferencesGroup, Label, Label, Label, Label) {
        let status_group = adw::PreferencesGroup::builder()
            .title("System Status")
            .build();

        let status_mode_row = adw::ActionRow::builder()
            .title("Graphics Mode")
            .subtitle("Current GPU operation mode")
            .build();
        let status_mode_val = Label::builder()
            .label("...")
            .css_classes(["status-value"])
            .valign(Align::Center)
            .build();
        status_mode_row.add_suffix(&status_mode_val);
        status_group.add(&status_mode_row);

        let status_cpu_row = adw::ActionRow::builder()
            .title("Active Threads")
            .subtitle("Online CPU cores")
            .build();
        let status_cpu_val = Label::builder()
            .label("...")
            .css_classes(["status-value-green"])
            .valign(Align::Center)
            .build();
        status_cpu_row.add_suffix(&status_cpu_val);
        status_group.add(&status_cpu_row);

        let status_hz_row = adw::ActionRow::builder()
            .title("Refresh Rate")
            .subtitle("Monitor Hz")
            .build();
        let hz_status_box = GtkBox::builder()
            .orientation(Orientation::Horizontal)
            .spacing(4)
            .valign(Align::Center)
            .build();
        let status_hz_val = Label::builder()
            .label("...")
            .css_classes(["status-value"])
            .build();
        hz_status_box.append(&status_hz_val);
        let native_badge = Label::builder()
            .label("NATIVE")
            .css_classes(["native-badge"])
            .visible(false)
            .build();
        hz_status_box.append(&native_badge);
        status_hz_row.add_suffix(&hz_status_box);
        status_group.add(&status_hz_row);

        (status_group, status_mode_val, status_cpu_val, status_hz_val, native_badge)
    }

    fn build_cpu_group() -> (adw::PreferencesGroup, adw::SpinRow, Button) {
        let cpu_group = adw::PreferencesGroup::builder()
            .title("Processor")
            .description("Limit active threads for power savings.")
            .build();

        let cpu_spin = adw::SpinRow::with_range(1.0, 16.0, 1.0);
        cpu_spin.set_title("CPU Thread Limit");
        cpu_spin.set_subtitle("Number of online logical cores");
        cpu_group.add(&cpu_spin);

        let cpu_apply_btn = Button::builder()
            .label("Apply")
            .margin_top(12)
            .css_classes(["suggested-action"])
            .sensitive(false)
            .build();
        cpu_group.add(&cpu_apply_btn);

        (cpu_group, cpu_spin, cpu_apply_btn)
    }

    fn build_gpu_group() -> (adw::PreferencesGroup, adw::ComboRow) {
        let gpu_group = adw::PreferencesGroup::builder()
            .title("Graphics")
            .description("Select GPU operation mode.")
            .build();

        let gpu_combo = adw::ComboRow::builder()
            .title("Graphics Mode")
            .subtitle("Requires logout to take effect")
            .sensitive(false)
            .build();
        gpu_combo.set_model(Some(&StringList::new(&["Loading..."])));
        gpu_group.add(&gpu_combo);

        (gpu_group, gpu_combo)
    }

    fn build_display_group() -> (adw::PreferencesGroup, adw::ComboRow) {
        let display_group = adw::PreferencesGroup::builder()
            .title("Display")
            .description("Control monitor refresh rate.")
            .build();

        let hz_combo = adw::ComboRow::builder()
            .title("Refresh Rate")
            .subtitle("Higher rates use more power")
            .sensitive(false)
            .build();
        hz_combo.set_model(Some(&StringList::new(&["Loading..."])));
        display_group.add(&hz_combo);

        (display_group, hz_combo)
    }

    fn setup_signals(&self) {
        let state = self.state.clone();
        let updating_ui = self.updating_ui.clone();
        let toast_overlay = self.toast_overlay.clone();
        let status_cpu_val = self.status_cpu_val.clone();
        let cpu_apply_btn = self.cpu_apply_btn.clone();
        let cpu_spin = self.cpu_spin.clone();

        self.cpu_apply_btn.connect_clicked(clone!(
            #[strong] state,
            #[strong] toast_overlay,
            #[strong] status_cpu_val,
            #[strong] cpu_apply_btn,
            #[strong] cpu_spin,
            move |_| {
                let target = cpu_spin.value() as u32;
                cpu_apply_btn.set_sensitive(false);
                show_toast(&toast_overlay, "Applying CPU settings...");

                let state_clone = state.clone();
                let toast_clone = toast_overlay.clone();
                let status_clone = status_cpu_val.clone();
                let btn_clone = cpu_apply_btn.clone();

                glib::spawn_future_local(async move {
                    let result = gio::spawn_blocking(move || {
                        system_info::apply_cpu_threads(target)
                    }).await;

                    btn_clone.set_sensitive(true);
                    
                    match result {
                        Ok(Ok(())) => {
                            let max = state_clone.borrow().max_cpu_threads;
                            status_clone.set_label(&format!("{}/{}", target, max));
                            state_clone.borrow_mut().current_cpu_threads = target;
                            show_toast(&toast_clone, "CPU thread limit applied.");
                        }
                        _ => {
                            show_toast(&toast_clone, "Failed to apply CPU settings.");
                        }
                    }
                });
            }
        ));

        let state = self.state.clone();
        let banner = self.banner.clone();

        self.gpu_combo.connect_selected_notify(clone!(
            #[strong] state,
            #[strong] updating_ui,
            #[strong] banner,
            move |combo| {
                if updating_ui.get() {
                    return;
                }
                
                let state_ref = state.borrow();
                if state_ref.gpu_modes.is_empty() {
                    return;
                }

                let idx = combo.selected() as usize;
                if idx >= state_ref.gpu_modes.len() {
                    return;
                }

                let new_mode = state_ref.gpu_modes[idx].clone();
                drop(state_ref);
                
                state.borrow_mut().pending_gpu_mode = new_mode.clone();
                
                let current = state.borrow().current_gpu_mode.clone();
                banner.set_revealed(new_mode != current);
            }
        ));

        let state = self.state.clone();
        let toast_overlay = self.toast_overlay.clone();
        let window = self.window.clone();

        self.banner.connect_button_clicked(clone!(
            #[strong] state,
            #[strong] toast_overlay,
            #[strong] window,
            move |_| {
                let pending = state.borrow().pending_gpu_mode.clone();
                let current = state.borrow().current_gpu_mode.clone();
                
                if pending == current {
                    return;
                }

                if !VALID_GPU_MODES.contains(pending.as_str()) {
                    show_toast(&toast_overlay, "Invalid GPU mode selected.");
                    return;
                }

                let dialog = adw::MessageDialog::builder()
                    .transient_for(&window)
                    .heading("Change Graphics Mode?")
                    .body(format!(
                        "Switching to {} mode will terminate your session immediately. You will lose unsaved work.",
                        pending
                    ))
                    .build();

                dialog.add_response("cancel", "Cancel");
                dialog.add_response("logout", "Switch & Log Out");
                dialog.set_response_appearance("logout", adw::ResponseAppearance::Destructive);
                dialog.set_default_response(Some("cancel"));
                dialog.set_close_response("cancel");

                let state_clone = state.clone();
                let toast_clone = toast_overlay.clone();

                dialog.connect_response(None, move |_, response| {
                    if response == "logout" {
                        let mode = state_clone.borrow().pending_gpu_mode.clone();
                        let toast = toast_clone.clone();
                        
                        glib::spawn_future_local(async move {
                            let mode_clone = mode.clone();
                            let result = gio::spawn_blocking(move || {
                                system_info::apply_gpu_mode(&mode_clone, true)
                            }).await;
                            
                            if let Ok(Err(e)) = result {
                                show_toast(&toast, &format!("GPU switch failed: {}", e));
                            }
                        });
                    }
                });

                dialog.present();
            }
        ));

        let state = self.state.clone();
        let updating_ui = self.updating_ui.clone();
        let toast_overlay = self.toast_overlay.clone();
        let status_hz_val = self.status_hz_val.clone();
        let native_badge = self.native_badge.clone();
        let hz_combo = self.hz_combo.clone();

        self.hz_combo.connect_selected_notify(clone!(
            #[strong] state,
            #[strong] updating_ui,
            #[strong] toast_overlay,
            #[strong] status_hz_val,
            #[strong] native_badge,
            #[strong] hz_combo,
            move |combo| {
                if updating_ui.get() {
                    return;
                }
                
                let state_ref = state.borrow();
                if state_ref.available_refresh_rates.is_empty() {
                    return;
                }

                let idx = combo.selected() as usize;
                if idx >= state_ref.available_refresh_rates.len() {
                    return;
                }

                let selected_rate = state_ref.available_refresh_rates[idx].clone();
                let new_hz = selected_rate.replace(" (Native)", "");
                let current = state_ref.current_refresh_rate.clone();
                let monitor = state_ref.monitor_name.clone();
                let native = state_ref.native_refresh_rate.clone();
                drop(state_ref);

                if new_hz == current {
                    return;
                }

                let hz_val: u32 = new_hz.replace("Hz", "").parse().unwrap_or(0);
                if hz_val == 0 {
                    show_toast(&toast_overlay, "Invalid refresh rate format");
                    return;
                }

                hz_combo.set_sensitive(false);

                let state_clone = state.clone();
                let toast_clone = toast_overlay.clone();
                let status_clone = status_hz_val.clone();
                let badge_clone = native_badge.clone();
                let combo_clone = hz_combo.clone();
                let native_clean = native.replace(" (Native)", "");
                let new_hz_clone = new_hz.clone();

                glib::spawn_future_local(async move {
                    let monitor_clone = monitor.clone();
                    let result = gio::spawn_blocking(move || {
                        system_info::apply_refresh_rate(&monitor_clone, hz_val)
                    }).await;

                    combo_clone.set_sensitive(true);

                    match result {
                        Ok(Ok(())) => {
                            state_clone.borrow_mut().current_refresh_rate = new_hz_clone.clone();
                            status_clone.set_label(&new_hz_clone);
                            badge_clone.set_visible(new_hz_clone == native_clean);
                            show_toast(&toast_clone, &format!("Refresh rate set to {}", new_hz_clone));
                        }
                        _ => {
                            show_toast(&toast_clone, "Failed to change refresh rate");
                        }
                    }
                });
            }
        ));
    }

    fn load_data(&self) {
        let state = self.state.clone();
        let updating_ui = self.updating_ui.clone();
        let status_mode_val = self.status_mode_val.clone();
        let status_cpu_val = self.status_cpu_val.clone();
        let status_hz_val = self.status_hz_val.clone();
        let native_badge = self.native_badge.clone();
        let cpu_spin = self.cpu_spin.clone();
        let cpu_apply_btn = self.cpu_apply_btn.clone();
        let gpu_combo = self.gpu_combo.clone();
        let hz_combo = self.hz_combo.clone();

        glib::spawn_future_local(async move {
            let info = gio::spawn_blocking(SystemInfo::fetch).await.unwrap_or_default();

            updating_ui.set(true);

            {
                let mut state_ref = state.borrow_mut();
                state_ref.max_cpu_threads = info.total_cpus;
                state_ref.current_cpu_threads = info.online_cpus;
                state_ref.current_gpu_mode = info.gpu_mode.clone();
                state_ref.pending_gpu_mode = info.gpu_mode.clone();
                state_ref.gpu_modes = info.supported_gpu_modes.clone();
                state_ref.available_refresh_rates = info.refresh_rates.clone();
                state_ref.current_refresh_rate = info.current_hz.clone();
                state_ref.native_refresh_rate = info.native_hz.clone();
                state_ref.monitor_name = info.monitor_name;
            }

            status_cpu_val.set_label(&format!("{}/{}", info.online_cpus, info.total_cpus));
            
            let adj = cpu_spin.adjustment();
            adj.set_upper(info.total_cpus as f64);
            cpu_spin.set_value(info.online_cpus as f64);
            cpu_apply_btn.set_sensitive(true);

            status_mode_val.set_label(&info.gpu_mode);

            if !info.supported_gpu_modes.is_empty() {
                let modes: Vec<&str> = info.supported_gpu_modes.iter().map(|s| s.as_str()).collect();
                gpu_combo.set_model(Some(&StringList::new(&modes)));
                
                if let Some(idx) = info.supported_gpu_modes.iter().position(|m| m == &info.gpu_mode) {
                    gpu_combo.set_selected(idx as u32);
                }
                gpu_combo.set_sensitive(true);
            } else {
                gpu_combo.set_subtitle("supergfxctl not found");
                gpu_combo.set_sensitive(false);
            }

            let hz_display = if info.current_hz.is_empty() {
                "Unknown".to_string()
            } else {
                info.current_hz.clone()
            };
            status_hz_val.set_label(&hz_display);

            let native_clean = info.native_hz.replace(" (Native)", "");
            native_badge.set_visible(info.current_hz == native_clean);

            if !info.refresh_rates.is_empty() {
                let rates: Vec<&str> = info.refresh_rates.iter().map(|s| s.as_str()).collect();
                hz_combo.set_model(Some(&StringList::new(&rates)));
                
                for (i, rate) in info.refresh_rates.iter().enumerate() {
                    let rate_clean = rate.replace(" (Native)", "");
                    if rate_clean == info.current_hz {
                        hz_combo.set_selected(i as u32);
                        break;
                    }
                }
                hz_combo.set_sensitive(true);
            } else {
                hz_combo.set_subtitle("Could not detect refresh rates");
                hz_combo.set_sensitive(false);
            }

            updating_ui.set(false);
        });
    }
}

fn show_toast(overlay: &adw::ToastOverlay, message: &str) {
    let toast = adw::Toast::new(message);
    overlay.add_toast(toast);
}
