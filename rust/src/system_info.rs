use once_cell::sync::Lazy;
use regex::Regex;
use serde::Deserialize;
use std::collections::HashSet;
use std::fs;
use std::process::Command;

pub const HELPER_PATH: &str = "/usr/libexec/tuxtuner-helper";

pub static VALID_GPU_MODES: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    ["Integrated", "Hybrid", "Dedicated", "Compute", "VFIO"]
        .into_iter()
        .collect()
});

pub static MONITOR_NAME_PATTERN: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z0-9_-]+$").unwrap());

pub static SESSION_ID_PATTERN: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[0-9]+$").unwrap());

#[derive(Debug, Clone, Default)]
pub struct SystemInfo {
    pub total_cpus: u32,
    pub online_cpus: u32,
    pub gpu_mode: String,
    pub supported_gpu_modes: Vec<String>,
    pub refresh_rates: Vec<String>,
    pub current_hz: String,
    pub native_hz: String,
    pub monitor_name: String,
}

#[derive(Debug, Deserialize)]
struct HyprMonitor {
    name: String,
    #[serde(rename = "refreshRate")]
    refresh_rate: f64,
    #[serde(rename = "availableModes")]
    available_modes: Vec<String>,
}

impl SystemInfo {
    pub fn fetch() -> Self {
        let (total_cpus, online_cpus) = Self::fetch_cpu_info();
        let (gpu_mode, supported_gpu_modes) = Self::fetch_gpu_info();
        let (refresh_rates, current_hz, native_hz, monitor_name) = Self::fetch_display_info();

        Self {
            total_cpus,
            online_cpus,
            gpu_mode,
            supported_gpu_modes,
            refresh_rates,
            current_hz,
            native_hz,
            monitor_name,
        }
    }

    fn fetch_cpu_info() -> (u32, u32) {
        let cpu_path = "/sys/devices/system/cpu";
        let mut total_cpus = 0u32;
        let mut online_cpus = 0u32;

        if let Ok(entries) = fs::read_dir(cpu_path) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name_str = name.to_string_lossy();

                if name_str.starts_with("cpu") {
                    if let Ok(num) = name_str[3..].parse::<u32>() {
                        total_cpus += 1;

                        if num == 0 {
                            online_cpus += 1;
                        } else {
                            let online_path = format!("{}/cpu{}/online", cpu_path, num);
                            if let Ok(content) = fs::read_to_string(&online_path) {
                                if content.trim() == "1" {
                                    online_cpus += 1;
                                }
                            }
                        }
                    }
                }
            }
        }

        if total_cpus == 0 {
            (16, 16)
        } else {
            (total_cpus, online_cpus)
        }
    }

    fn fetch_gpu_info() -> (String, Vec<String>) {
        let mut gpu_mode = String::from("Integrated");
        let mut supported_modes = Vec::new();

        if let Ok(output) = Command::new("supergfxctl").arg("-s").output() {
            if output.status.success() {
                let raw = String::from_utf8_lossy(&output.stdout);
                let raw = raw.trim().trim_start_matches('[').trim_end_matches(']');
                supported_modes = raw.split(',').map(|s| s.trim().to_string()).collect();
            }
        }

        if let Ok(output) = Command::new("supergfxctl").arg("-g").output() {
            if output.status.success() {
                gpu_mode = String::from_utf8_lossy(&output.stdout).trim().to_string();
            }
        }

        if supported_modes.is_empty() {
            gpu_mode = String::from("Unavailable");
        }

        (gpu_mode, supported_modes)
    }

    fn fetch_display_info() -> (Vec<String>, String, String, String) {
        let mut refresh_rates = Vec::new();
        let mut current_hz = String::new();
        let mut native_hz = String::new();
        let mut monitor_name = String::new();

        if let Ok(output) = Command::new("hyprctl").args(["monitors", "-j"]).output() {
            if output.status.success() {
                let json_str = String::from_utf8_lossy(&output.stdout);
                if let Ok(monitors) = serde_json::from_str::<Vec<HyprMonitor>>(&json_str) {
                    if let Some(mon) = monitors.first() {
                        monitor_name = mon.name.clone();
                        current_hz = format!("{}Hz", mon.refresh_rate as u32);

                        let mut hz_values: Vec<f64> = Vec::new();
                        for mode in &mon.available_modes {
                            if let Some(hz_part) = mode.split('@').nth(1) {
                                let hz_str = hz_part.replace("Hz", "");
                                if let Ok(hz_val) = hz_str.parse::<f64>() {
                                    if !hz_values.iter().any(|&v| (v - hz_val).abs() < 0.5) {
                                        hz_values.push(hz_val);
                                    }
                                }
                            }
                        }

                        hz_values.sort_by(|a, b| a.partial_cmp(b).unwrap());

                        if let Some(&max_hz) = hz_values.last() {
                            native_hz = format!("{}Hz", max_hz as u32);
                        }

                        for hz_val in hz_values {
                            let hz_str = format!("{}Hz", hz_val as u32);
                            if hz_str == native_hz {
                                refresh_rates.push(format!("{}Hz (Native)", hz_val as u32));
                            } else {
                                refresh_rates.push(hz_str);
                            }
                        }
                    }
                }
            }
        }

        (refresh_rates, current_hz, native_hz, monitor_name)
    }
}

pub fn apply_cpu_threads(target: u32) -> Result<(), String> {
    let output = Command::new("pkexec")
        .args([HELPER_PATH, "cpu", &target.to_string()])
        .output()
        .map_err(|e| e.to_string())?;

    if output.status.success() {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).to_string())
    }
}

pub fn apply_gpu_mode(mode: &str, logout: bool) -> Result<(), String> {
    if !VALID_GPU_MODES.contains(mode) {
        return Err(format!("Invalid GPU mode: {}", mode));
    }

    let mut args = vec![HELPER_PATH, "gpu", mode];

    let session_id = std::env::var("XDG_SESSION_ID").ok();
    if logout {
        if let Some(ref sid) = session_id {
            if SESSION_ID_PATTERN.is_match(sid) {
                args.push("--logout");
                args.push(sid);
            }
        }
    }

    let output = Command::new("pkexec")
        .args(&args)
        .output()
        .map_err(|e| e.to_string())?;

    if output.status.success() {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).to_string())
    }
}

pub fn apply_refresh_rate(monitor: &str, hz: u32) -> Result<(), String> {
    if !MONITOR_NAME_PATTERN.is_match(monitor) {
        return Err("Invalid monitor name".to_string());
    }

    if !(30..=500).contains(&hz) {
        return Err("Refresh rate out of valid range".to_string());
    }

    let monitor_arg = format!("{},preferred@{},auto,1", monitor, hz);

    let output = Command::new("hyprctl")
        .args(["keyword", "monitor", &monitor_arg])
        .output()
        .map_err(|e| e.to_string())?;

    if output.status.success() {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).to_string())
    }
}
