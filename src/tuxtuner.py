#!/usr/bin/env python3
import sys
import os
import re
import subprocess
import threading
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio, Gdk

HELPER_PATH = "/usr/local/libexec/tuxtuner-helper"

VALID_GPU_MODES = {"Integrated", "Hybrid", "Dedicated", "Compute", "VFIO"}
MONITOR_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
SESSION_ID_PATTERN = re.compile(r"^[0-9]+$")

APP_CSS = """
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

.hz-option-native {
    font-weight: 700;
}
"""


class TuxTunerApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.github.xavrir.TuxTuner",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )

    def do_startup(self):
        Adw.Application.do_startup(self)
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(APP_CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TuxTunerWindow(application=self)
        win.present()


class TuxTunerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(460, 680)
        self.set_title("TuxTuner")

        self.current_cpu_threads = 0
        self.max_cpu_threads = 16
        self.current_gpu_mode = "Unknown"
        self.pending_gpu_mode = "Unknown"
        self.gpu_modes = []
        self.available_refresh_rates = []
        self.current_refresh_rate = ""
        self.native_refresh_rate = ""
        self.monitor_name = ""
        self._updating_ui = False

        self.main_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.main_content)
        self.set_content(self.toast_overlay)

        self._build_header()
        self._build_banner()
        self._build_content()

        self.load_data()

    def _build_header(self):
        header_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, css_classes=["tuxtuner-header"]
        )
        header_box.set_halign(Gtk.Align.FILL)

        penguin_label = Gtk.Label(label="🐧", css_classes=["tuxtuner-penguin"])
        header_box.append(penguin_label)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title_box.set_valign(Gtk.Align.CENTER)

        title_label = Gtk.Label(label="TUXTUNER", css_classes=["tuxtuner-title"])
        title_label.set_halign(Gtk.Align.START)
        title_box.append(title_label)

        subtitle_label = Gtk.Label(
            label="SYSTEM PERFORMANCE CONTROL", css_classes=["tuxtuner-subtitle"]
        )
        subtitle_label.set_halign(Gtk.Align.START)
        title_box.append(subtitle_label)

        header_box.append(title_box)
        self.main_content.append(header_box)

    def _build_banner(self):
        self.banner = Adw.Banner(title="Graphics mode change requires logout.")
        self.banner.set_button_label("Switch & Log Out")
        self.banner.connect("button-clicked", self.on_gpu_switch_confirm)
        self.main_content.append(self.banner)

    def _build_content(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.main_content.append(scroll)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.set_margin_top(8)
        content_box.set_margin_bottom(24)
        scroll.set_child(content_box)

        page = Adw.PreferencesPage()
        content_box.append(page)

        self._build_status_group(page)
        self._build_cpu_group(page)
        self._build_gpu_group(page)
        self._build_display_group(page)

    def _build_status_group(self, page):
        status_group = Adw.PreferencesGroup(title="System Status")
        page.add(status_group)

        self.status_mode_row = Adw.ActionRow(
            title="Graphics Mode", subtitle="Current GPU operation mode"
        )
        self.status_mode_val = Gtk.Label(label="...", css_classes=["status-value"])
        self.status_mode_val.set_valign(Gtk.Align.CENTER)
        self.status_mode_row.add_suffix(self.status_mode_val)
        status_group.add(self.status_mode_row)

        self.status_cpu_row = Adw.ActionRow(
            title="Active Threads", subtitle="Online CPU cores"
        )
        self.status_cpu_val = Gtk.Label(label="...", css_classes=["status-value-green"])
        self.status_cpu_val.set_valign(Gtk.Align.CENTER)
        self.status_cpu_row.add_suffix(self.status_cpu_val)
        status_group.add(self.status_cpu_row)

        self.status_hz_row = Adw.ActionRow(title="Refresh Rate", subtitle="Monitor Hz")
        self.hz_status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.hz_status_box.set_valign(Gtk.Align.CENTER)
        self.status_hz_val = Gtk.Label(label="...", css_classes=["status-value"])
        self.hz_status_box.append(self.status_hz_val)
        self.native_badge = Gtk.Label(label="NATIVE", css_classes=["native-badge"])
        self.native_badge.set_visible(False)
        self.hz_status_box.append(self.native_badge)
        self.status_hz_row.add_suffix(self.hz_status_box)
        status_group.add(self.status_hz_row)

    def _build_cpu_group(self, page):
        cpu_group = Adw.PreferencesGroup(
            title="Processor", description="Limit active threads for power savings."
        )
        page.add(cpu_group)

        self.cpu_spin = Adw.SpinRow.new_with_range(1, 16, 1)
        self.cpu_spin.set_title("CPU Thread Limit")
        self.cpu_spin.set_subtitle("Number of online logical cores")
        cpu_group.add(self.cpu_spin)

        self.cpu_apply_btn = Gtk.Button(label="Apply")
        self.cpu_apply_btn.set_margin_top(12)
        self.cpu_apply_btn.add_css_class("suggested-action")
        self.cpu_apply_btn.connect("clicked", self.on_cpu_apply)
        cpu_group.add(self.cpu_apply_btn)

    def _build_gpu_group(self, page):
        gpu_group = Adw.PreferencesGroup(
            title="Graphics", description="Select GPU operation mode."
        )
        page.add(gpu_group)

        self.gpu_combo = Adw.ComboRow(title="Graphics Mode")
        self.gpu_combo.set_subtitle("Requires logout to take effect")
        self.gpu_combo.set_model(Gtk.StringList.new(["Loading..."]))
        self.gpu_combo.connect("notify::selected-item", self.on_gpu_changed)
        gpu_group.add(self.gpu_combo)

    def _build_display_group(self, page):
        display_group = Adw.PreferencesGroup(
            title="Display", description="Control monitor refresh rate."
        )
        page.add(display_group)

        self.hz_combo = Adw.ComboRow(title="Refresh Rate")
        self.hz_combo.set_subtitle("Higher rates use more power")
        self.hz_combo.set_model(Gtk.StringList.new(["Loading..."]))
        self.hz_combo.connect("notify::selected-item", self.on_hz_changed)
        display_group.add(self.hz_combo)

    def load_data(self):
        self.cpu_apply_btn.set_sensitive(False)
        self.gpu_combo.set_sensitive(False)
        self.hz_combo.set_sensitive(False)

        thread = threading.Thread(target=self._fetch_system_info)
        thread.daemon = True
        thread.start()

    def _fetch_system_info(self):
        try:
            total_cpus = 0
            online_cpus = 0
            if os.path.exists("/sys/devices/system/cpu"):
                for name in os.listdir("/sys/devices/system/cpu"):
                    if name.startswith("cpu") and name[3:].isdigit():
                        total_cpus += 1
                        online_path = f"/sys/devices/system/cpu/{name}/online"
                        if name == "cpu0":
                            online_cpus += 1
                        elif os.path.exists(online_path):
                            with open(online_path, "r") as f:
                                if f.read().strip() == "1":
                                    online_cpus += 1
        except Exception as e:
            print(f"Error reading CPU info: {e}")
            total_cpus = 16
            online_cpus = 16

        gpu_mode = "Integrated"
        supported_modes = []
        try:
            res = subprocess.run(["supergfxctl", "-s"], capture_output=True, text=True)
            if res.returncode == 0:
                raw = res.stdout.strip().strip("[]")
                supported_modes = [m.strip() for m in raw.split(",")]

            res = subprocess.run(["supergfxctl", "-g"], capture_output=True, text=True)
            if res.returncode == 0:
                gpu_mode = res.stdout.strip()
        except FileNotFoundError:
            gpu_mode = "Unavailable"
            supported_modes = []

        refresh_rates = []
        current_hz = ""
        native_hz = ""
        monitor_name = ""
        try:
            import json

            res = subprocess.run(
                ["hyprctl", "monitors", "-j"], capture_output=True, text=True
            )
            if res.returncode == 0:
                monitors = json.loads(res.stdout)
                if monitors:
                    mon = monitors[0]
                    monitor_name = mon.get("name", "")
                    current_hz = f"{mon.get('refreshRate', 0):.0f}Hz"

                    hz_values = []
                    for mode in mon.get("availableModes", []):
                        if "@" in mode:
                            hz_part = mode.split("@")[1].replace("Hz", "")
                            try:
                                hz_val = float(hz_part)
                                if hz_val not in hz_values:
                                    hz_values.append(hz_val)
                            except ValueError:
                                pass

                    hz_values.sort()
                    if hz_values:
                        native_hz = f"{int(hz_values[-1])}Hz"

                    for hz_val in hz_values:
                        hz_str = f"{int(hz_val)}Hz"
                        if hz_str == native_hz:
                            hz_str = f"{int(hz_val)}Hz (Native)"
                        refresh_rates.append(hz_str)

        except Exception as e:
            print(f"Error reading display info: {e}")

        GLib.idle_add(
            self._update_ui_state,
            total_cpus,
            online_cpus,
            gpu_mode,
            supported_modes,
            refresh_rates,
            current_hz,
            native_hz,
            monitor_name,
        )

    def _update_ui_state(
        self,
        total_cpus,
        online_cpus,
        gpu_mode,
        supported_modes,
        refresh_rates,
        current_hz,
        native_hz,
        monitor_name,
    ):
        self._updating_ui = True

        self.max_cpu_threads = total_cpus
        self.current_cpu_threads = online_cpus
        self.current_gpu_mode = gpu_mode
        self.pending_gpu_mode = gpu_mode
        self.available_refresh_rates = refresh_rates
        self.current_refresh_rate = current_hz
        self.native_refresh_rate = native_hz
        self.monitor_name = monitor_name

        self.status_cpu_val.set_label(f"{online_cpus}/{total_cpus}")

        adj = self.cpu_spin.get_adjustment()
        adj.set_upper(total_cpus)
        self.cpu_spin.set_value(online_cpus)
        self.cpu_apply_btn.set_sensitive(True)

        self.status_mode_val.set_label(gpu_mode)

        if supported_modes:
            self.gpu_modes = supported_modes
            str_list = Gtk.StringList.new(supported_modes)
            self.gpu_combo.set_model(str_list)
            try:
                idx = supported_modes.index(gpu_mode)
                self.gpu_combo.set_selected(idx)
            except ValueError:
                pass
            self.gpu_combo.set_sensitive(True)
        else:
            self.gpu_combo.set_subtitle("supergfxctl not found")
            self.gpu_combo.set_sensitive(False)

        self.status_hz_val.set_label(current_hz if current_hz else "Unknown")

        is_native = (
            current_hz == native_hz.replace(" (Native)", "") if native_hz else False
        )
        self.native_badge.set_visible(is_native)

        if refresh_rates:
            str_list = Gtk.StringList.new(refresh_rates)
            self.hz_combo.set_model(str_list)

            current_idx = -1
            for i, rate in enumerate(refresh_rates):
                rate_clean = rate.replace(" (Native)", "")
                if rate_clean == current_hz:
                    current_idx = i
                    break

            if current_idx >= 0:
                self.hz_combo.set_selected(current_idx)
            self.hz_combo.set_sensitive(True)
        else:
            self.hz_combo.set_subtitle("Could not detect refresh rates")
            self.hz_combo.set_sensitive(False)

        self._updating_ui = False

    def on_cpu_apply(self, btn):
        target = int(self.cpu_spin.get_value())
        self.cpu_apply_btn.set_sensitive(False)
        self.show_toast("Applying CPU settings...")

        def run_cpu_helper():
            try:
                subprocess.run(["pkexec", HELPER_PATH, "cpu", str(target)], check=True)
                success = True
            except subprocess.CalledProcessError:
                success = False
            GLib.idle_add(self._on_cpu_applied, success, target)

        threading.Thread(target=run_cpu_helper).start()

    def _on_cpu_applied(self, success, target):
        self.cpu_apply_btn.set_sensitive(True)
        if success:
            self.show_toast("CPU thread limit applied.")
            self.status_cpu_val.set_label(f"{target}/{self.max_cpu_threads}")
            self.current_cpu_threads = target
        else:
            self.show_toast("Failed to apply CPU settings.")

    def on_gpu_changed(self, row, param):
        if self._updating_ui or not self.gpu_modes:
            return

        selected_idx = self.gpu_combo.get_selected()
        if selected_idx < 0 or selected_idx >= len(self.gpu_modes):
            return

        new_mode = self.gpu_modes[selected_idx]
        self.pending_gpu_mode = new_mode

        if new_mode != self.current_gpu_mode:
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def on_gpu_switch_confirm(self, banner):
        if self.pending_gpu_mode == self.current_gpu_mode:
            return

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Change Graphics Mode?",
            body=f"Switching to {self.pending_gpu_mode} mode will terminate your session immediately. You will lose unsaved work.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("logout", "Switch & Log Out")
        dialog.set_response_appearance("logout", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_gpu_dialog_response)
        dialog.present()

    def on_gpu_dialog_response(self, dialog, response):
        if response == "logout":
            self.execute_gpu_switch()

    def execute_gpu_switch(self):
        try:
            if self.pending_gpu_mode not in VALID_GPU_MODES:
                self.show_toast("Invalid GPU mode selected.")
                return

            session_id = os.environ.get("XDG_SESSION_ID", "")

            if session_id and not SESSION_ID_PATTERN.match(session_id):
                self.show_toast("Invalid session ID detected.")
                return

            cmd = ["pkexec", HELPER_PATH, "gpu", self.pending_gpu_mode]
            if session_id:
                cmd.extend(["--logout", session_id])

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                GLib.idle_add(self.show_toast, f"GPU switch failed: {error_msg}")
        except Exception as e:
            self.show_toast(f"Failed to switch GPU mode: {e}")

    def show_toast(self, message):
        toast = Adw.Toast.new(message)
        self.toast_overlay.add_toast(toast)

    def on_hz_changed(self, row, param):
        if self._updating_ui or not self.available_refresh_rates:
            return

        selected_idx = self.hz_combo.get_selected()
        if selected_idx < 0 or selected_idx >= len(self.available_refresh_rates):
            return

        selected_rate = self.available_refresh_rates[selected_idx]
        new_hz = selected_rate.replace(" (Native)", "")

        if new_hz != self.current_refresh_rate:
            self.apply_refresh_rate(new_hz)

    def apply_refresh_rate(self, hz_str):
        if not self.monitor_name:
            self.show_toast("No monitor detected")
            return

        if not MONITOR_NAME_PATTERN.match(self.monitor_name):
            self.show_toast("Invalid monitor name detected")
            return

        try:
            hz_val = int(hz_str.replace("Hz", ""))
        except ValueError:
            self.show_toast("Invalid refresh rate format")
            return

        if not (30 <= hz_val <= 500):
            self.show_toast("Refresh rate out of valid range")
            return

        self.hz_combo.set_sensitive(False)

        def run_hz_change():
            try:
                subprocess.run(
                    [
                        "hyprctl",
                        "keyword",
                        "monitor",
                        f"{self.monitor_name},preferred@{hz_val},auto,1",
                    ],
                    check=True,
                    capture_output=True,
                )
                success = True
            except subprocess.CalledProcessError:
                success = False
            GLib.idle_add(self._on_hz_applied, success, hz_str)

        threading.Thread(target=run_hz_change).start()

    def _on_hz_applied(self, success, hz_str):
        self.hz_combo.set_sensitive(True)
        if success:
            self.current_refresh_rate = hz_str
            self.status_hz_val.set_label(hz_str)

            native_clean = self.native_refresh_rate.replace(" (Native)", "")
            is_native = hz_str == native_clean
            self.native_badge.set_visible(is_native)

            self.show_toast(f"Refresh rate set to {hz_str}")
        else:
            self.show_toast("Failed to change refresh rate")
            self.load_data()


if __name__ == "__main__":
    app = TuxTunerApp()
    app.run(sys.argv)
