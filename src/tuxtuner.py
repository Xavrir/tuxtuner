#!/usr/bin/env python3
import sys
import os
import re
import subprocess
import threading
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio

HELPER_PATH = "/usr/local/libexec/tuxtuner-helper"

# Security: Valid GPU modes (must match tuxtuner-helper allowlist)
VALID_GPU_MODES = {"Integrated", "Hybrid", "Dedicated", "Compute", "VFIO"}

# Security: Regex patterns for input validation
MONITOR_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
SESSION_ID_PATTERN = re.compile(r"^[0-9]+$")


class TuxTunerApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.github.xavrir.TuxTuner",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TuxTunerWindow(application=self)
        win.present()


class TuxTunerWindow(Adw.PreferencesWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(500, 700)
        self.set_title("TuxTuner")

        # Internal State
        self.current_cpu_threads = 0
        self.max_cpu_threads = 16
        self.current_gpu_mode = "Unknown"
        self.pending_gpu_mode = "Unknown"
        self.gpu_modes = []
        self.available_refresh_rates = []
        self.current_refresh_rate = ""
        self.monitor_name = ""

        # Toast Overlay
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        # Main Layout Box (Vertical)
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(self.main_box)

        # 1. Banner for GPU Logout Warning
        self.banner = Adw.Banner(title="Graphics mode change requires logout.")
        self.banner.set_button_label("Switch & Log Out")
        self.banner.connect("button-clicked", self.on_gpu_switch_confirm)
        self.main_box.append(self.banner)

        # 2. Preferences Page
        self.page = Adw.PreferencesPage()
        self.main_box.append(self.page)

        # --- Status Group ---
        self.status_group = Adw.PreferencesGroup(title="Status")
        self.page.add(self.status_group)

        self.status_mode_row = Adw.ActionRow(title="Current Mode")
        self.status_mode_val = Gtk.Label(label="...", css_classes=["dim-label"])
        self.status_mode_row.add_suffix(self.status_mode_val)
        self.status_group.add(self.status_mode_row)

        self.status_cpu_row = Adw.ActionRow(title="Active Threads")
        self.status_cpu_val = Gtk.Label(label="...", css_classes=["dim-label"])
        self.status_cpu_row.add_suffix(self.status_cpu_val)
        self.status_group.add(self.status_cpu_row)

        self.status_hz_row = Adw.ActionRow(title="Refresh Rate")
        self.status_hz_val = Gtk.Label(label="...", css_classes=["dim-label"])
        self.status_hz_row.add_suffix(self.status_hz_val)
        self.status_group.add(self.status_hz_row)

        # --- Processor Group ---
        self.cpu_group = Adw.PreferencesGroup(
            title="Processor", description="Limit active threads for power savings."
        )
        self.page.add(self.cpu_group)

        # CPU SpinRow
        self.cpu_spin = Adw.SpinRow.new_with_range(1, 16, 1)
        self.cpu_spin.set_title("CPU Thread Limit")
        self.cpu_spin.set_subtitle("Number of online logical cores")
        self.cpu_group.add(self.cpu_spin)

        # CPU Apply Button
        self.cpu_apply_btn = Gtk.Button(label="Apply", margin_top=10)
        self.cpu_apply_btn.add_css_class("suggested-action")
        self.cpu_apply_btn.connect("clicked", self.on_cpu_apply)
        self.cpu_group.add(self.cpu_apply_btn)

        # --- Graphics Group ---
        self.gpu_group = Adw.PreferencesGroup(
            title="Graphics", description="Select GPU operation mode."
        )
        self.page.add(self.gpu_group)

        # GPU ComboRow
        self.gpu_combo = Adw.ComboRow(title="Graphics Mode")
        self.gpu_combo.set_subtitle("Requires logout to take effect")
        self.gpu_combo.set_model(Gtk.StringList.new(["Loading..."]))
        self.gpu_combo.connect("notify::selected-item", self.on_gpu_changed)
        self.gpu_group.add(self.gpu_combo)

        # --- Display Group ---
        self.display_group = Adw.PreferencesGroup(
            title="Display", description="Control monitor refresh rate."
        )
        self.page.add(self.display_group)

        # Refresh Rate ComboRow
        self.hz_combo = Adw.ComboRow(title="Refresh Rate")
        self.hz_combo.set_subtitle("Higher rates use more power")
        self.hz_combo.set_model(Gtk.StringList.new(["Loading..."]))
        self.hz_combo.connect("notify::selected-item", self.on_hz_changed)
        self.display_group.add(self.hz_combo)

        # Initial Data Load
        self.load_data()

    def load_data(self):
        # Disable controls while loading
        self.cpu_apply_btn.set_sensitive(False)
        self.gpu_combo.set_sensitive(False)

        # Fetch in thread
        thread = threading.Thread(target=self._fetch_system_info)
        thread.daemon = True
        thread.start()

    def _fetch_system_info(self):
        # 1. Get CPU Info
        # Count total possible cpus
        try:
            total_cpus = 0
            online_cpus = 0
            if os.path.exists("/sys/devices/system/cpu"):
                for name in os.listdir("/sys/devices/system/cpu"):
                    if name.startswith("cpu") and name[3:].isdigit():
                        total_cpus += 1
                        # Check online
                        online_path = f"/sys/devices/system/cpu/{name}/online"
                        # cpu0 is usually always online and might not have 'online' file
                        if name == "cpu0":
                            online_cpus += 1
                        elif os.path.exists(online_path):
                            with open(online_path, "r") as f:
                                if f.read().strip() == "1":
                                    online_cpus += 1
        except Exception as e:
            print(f"Error reading CPU info: {e}")
            total_cpus = 16  # Fallback
            online_cpus = 16

        # 2. Get GPU Info
        gpu_mode = "Integrated"
        supported_modes = []
        try:
            # Check supported
            res = subprocess.run(["supergfxctl", "-s"], capture_output=True, text=True)
            if res.returncode == 0:
                # Output format: [Integrated, Hybrid, ...]
                raw = res.stdout.strip().strip("[]")
                supported_modes = [m.strip() for m in raw.split(",")]

            # Get current
            res = subprocess.run(["supergfxctl", "-g"], capture_output=True, text=True)
            if res.returncode == 0:
                gpu_mode = res.stdout.strip()
        except FileNotFoundError:
            gpu_mode = "Unavailable"
            supported_modes = []

        # 3. Get Display/Refresh Rate Info (Hyprland)
        refresh_rates = []
        current_hz = ""
        monitor_name = ""
        try:
            import json

            res = subprocess.run(
                ["hyprctl", "monitors", "-j"], capture_output=True, text=True
            )
            if res.returncode == 0:
                monitors = json.loads(res.stdout)
                if monitors:
                    mon = monitors[0]  # Primary monitor
                    monitor_name = mon.get("name", "")
                    current_hz = f"{mon.get('refreshRate', 0):.0f}Hz"
                    # Parse available modes
                    for mode in mon.get("availableModes", []):
                        # Format: "1920x1200@165.01Hz"
                        if "@" in mode:
                            hz_part = mode.split("@")[1].replace("Hz", "")
                            try:
                                hz_val = float(hz_part)
                                hz_str = f"{hz_val:.0f}Hz"
                                if hz_str not in refresh_rates:
                                    refresh_rates.append(hz_str)
                            except ValueError:
                                pass
                    refresh_rates.sort(key=lambda x: int(x.replace("Hz", "")))
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
        monitor_name,
    ):
        self.max_cpu_threads = total_cpus
        self.current_cpu_threads = online_cpus
        self.current_gpu_mode = gpu_mode
        self.pending_gpu_mode = gpu_mode  # Initially same
        self.available_refresh_rates = refresh_rates
        self.current_refresh_rate = current_hz
        self.monitor_name = monitor_name

        # Update CPU UI
        self.status_cpu_val.set_label(f"{online_cpus}/{total_cpus}")

        # Configure SpinRow
        # AdwSpinRow adjustment: value, lower, upper, step, page, size
        adj = self.cpu_spin.get_adjustment()
        adj.set_upper(total_cpus)
        self.cpu_spin.set_value(online_cpus)
        self.cpu_apply_btn.set_sensitive(True)

        # Update GPU UI
        self.status_mode_val.set_label(gpu_mode)

        if supported_modes:
            self.gpu_modes = supported_modes
            str_list = Gtk.StringList.new(supported_modes)
            self.gpu_combo.set_model(str_list)

            # Select current mode
            try:
                idx = supported_modes.index(gpu_mode)
                self.gpu_combo.set_selected(idx)
            except ValueError:
                pass
            self.gpu_combo.set_sensitive(True)
        else:
            self.gpu_combo.set_subtitle("supergfxctl not found")
            self.gpu_combo.set_sensitive(False)

        # Update Display/Refresh Rate UI
        self.status_hz_val.set_label(current_hz if current_hz else "Unknown")

        if refresh_rates:
            str_list = Gtk.StringList.new(refresh_rates)
            self.hz_combo.set_model(str_list)

            # Select current rate
            try:
                idx = refresh_rates.index(current_hz)
                self.hz_combo.set_selected(idx)
            except ValueError:
                pass
            self.hz_combo.set_sensitive(True)
        else:
            self.hz_combo.set_subtitle("Could not detect refresh rates")
            self.hz_combo.set_sensitive(False)

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
        if not self.gpu_modes:
            return

        selected_idx = self.gpu_combo.get_selected()
        if selected_idx < 0 or selected_idx >= len(self.gpu_modes):
            return

        new_mode = self.gpu_modes[selected_idx]
        self.pending_gpu_mode = new_mode

        # Show banner if mode is different from current system state
        if new_mode != self.current_gpu_mode:
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def on_gpu_switch_confirm(self, banner):
        if self.pending_gpu_mode == self.current_gpu_mode:
            return

        # Confirm dialog
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
        if not self.available_refresh_rates:
            return

        selected_idx = self.hz_combo.get_selected()
        if selected_idx < 0 or selected_idx >= len(self.available_refresh_rates):
            return

        new_hz = self.available_refresh_rates[selected_idx]

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
        if success:
            self.current_refresh_rate = hz_str
            self.status_hz_val.set_label(hz_str)
            self.show_toast(f"Refresh rate set to {hz_str}")
        else:
            self.show_toast("Failed to change refresh rate")


if __name__ == "__main__":
    app = TuxTunerApp()
    app.run(sys.argv)
