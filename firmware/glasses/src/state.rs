//! What the glasses are doing now: the recording, the rate, and settings as they arrive.

use std::time::Duration;

use crate::display::{Display, Look};

pub const DEFAULT_FRAMES_PER_MINUTE: f64 = 30.0;

pub struct StorageUpload {
    pub url: String,
    pub object_name: String,
}

pub struct State {
    pub frames_per_minute: f64,
    pub recording: bool,
    pub recording_id: String,
    pub frames_sent: u32,
    pub status_text: Option<String>,
    pub recording_to_end: Option<String>,
}

impl State {
    pub fn new() -> Self {
        State {
            frames_per_minute: DEFAULT_FRAMES_PER_MINUTE,
            recording: true,
            recording_id: new_recording_id(),
            frames_sent: 0,
            status_text: None,
            recording_to_end: None,
        }
    }

    pub fn apply_settings(&mut self, settings_json: &[u8], display: &mut Display) -> Option<Option<StorageUpload>> {
        let json = serde_json::from_slice::<serde_json::Value>(settings_json).ok()?;
        let current = display.look.clone();
        let number = |name: &str, current: f64| json[name].as_f64().unwrap_or(current);
        let flag = |name: &str, current: bool| json[name].as_bool().unwrap_or(current);

        let recording = flag("recording", self.recording);
        if recording && !self.recording {
            self.recording_id = new_recording_id();
            self.frames_sent = 0;
        }

        if !recording && self.recording && self.frames_sent > 0 {
            self.recording_to_end = Some(self.recording_id.clone());
        }

        self.frames_per_minute = number("frames_per_minute", self.frames_per_minute);
        self.recording = recording;

        if let Some(text) = json["status"].as_str() {
            self.status_text = Some(text.to_string()).filter(|text| !text.is_empty());
        }

        display.set_look(Look {
            scale: number("display_scale", current.scale),
            offset_x: number("display_x", current.offset_x),
            offset_y: number("display_y", current.offset_y),
            brightness: number("display_brightness", current.brightness),
            large_text: flag("display_large_text", current.large_text),
            mirror: flag("display_mirror", current.mirror),
            flip: flag("display_flip", current.flip),
            focus_pattern: flag("display_pattern", current.focus_pattern),
        });

        Some(match (json["upload_url"].as_str(), json["upload_name"].as_str()) {
            (Some(url), Some(name)) => Some(StorageUpload { url: url.to_string(), object_name: name.to_string() }),
            _ => None,
        })
    }

    pub fn frame_interval(&self) -> Duration {
        Duration::from_secs_f64(60.0 / self.frames_per_minute.max(0.01))
    }

    pub fn status_line(&self) -> String {
        self.status_text.clone().unwrap_or_else(|| format!("Rec {}", self.frames_sent))
    }
}

pub fn new_recording_id() -> String {
    unsafe { format!("{:08x}{:08x}", esp_idf_svc::sys::esp_random(), esp_idf_svc::sys::esp_random()) }
}
