//! What the glasses are doing now: the recording, the rate, and settings as they arrive.

use std::time::Duration;

use crate::display::{Display, Look};

pub const DEFAULT_FRAMES_PER_MINUTE: f64 = 30.0;

pub struct Upload {
    pub url: String,
    pub name: String,
}

pub struct State {
    pub rate: f64,
    pub recording: bool,
    pub recording_id: String,
    pub sent: u32,
    pub status: Option<String>,
    pub stopped: Option<String>,
}

impl State {
    pub fn new() -> Self {
        State { rate: DEFAULT_FRAMES_PER_MINUTE, recording: true, recording_id: new_recording_id(), sent: 0, status: None, stopped: None }
    }

    pub fn apply(&mut self, reply: &[u8], display: &mut Display) -> Option<Option<Upload>> {
        let json = serde_json::from_slice::<serde_json::Value>(reply).ok()?;
        let now = display.look.clone();
        let number = |name: &str, current: f64| json[name].as_f64().unwrap_or(current);
        let flag = |name: &str, current: bool| json[name].as_bool().unwrap_or(current);

        let recording = flag("recording", self.recording);
        if recording && !self.recording {
            self.recording_id = new_recording_id();
            self.sent = 0;
        }

        if !recording && self.recording && self.sent > 0 {
            self.stopped = Some(self.recording_id.clone());
        }

        self.rate = number("frames_per_minute", self.rate);
        self.recording = recording;

        if let Some(status) = json["status"].as_str() {
            self.status = Some(status.to_string()).filter(|text| !text.is_empty());
        }

        display.set_look(Look {
            scale: number("display_scale", now.scale),
            x: number("display_x", now.x),
            y: number("display_y", now.y),
            brightness: number("display_brightness", now.brightness),
            large_text: flag("display_large_text", now.large_text),
            mirror: flag("display_mirror", now.mirror),
            flip: flag("display_flip", now.flip),
            pattern: flag("display_pattern", now.pattern),
        });

        Some(match (json["upload_url"].as_str(), json["upload_name"].as_str()) {
            (Some(url), Some(name)) => Some(Upload { url: url.to_string(), name: name.to_string() }),
            _ => None,
        })
    }

    pub fn gap(&self) -> Duration {
        Duration::from_secs_f64(60.0 / self.rate.max(0.01))
    }

    pub fn line(&self) -> String {
        self.status.clone().unwrap_or_else(|| format!("Rec {}", self.sent))
    }
}

pub fn new_recording_id() -> String {
    unsafe { format!("{:08x}{:08x}", esp_idf_svc::sys::esp_random(), esp_idf_svc::sys::esp_random()) }
}
