//! Wi-Fi mode: join the network and send photos to the server or straight to storage.

use std::thread;
use std::time::{Duration, Instant};
use esp_idf_svc::http::client::{Configuration as HttpConfiguration, EspHttpConnection};
use esp_idf_svc::http::Method;
use esp_idf_svc::sys::EspError;
use esp_idf_svc::wifi::{AuthMethod, BlockingWifi, ClientConfiguration, Configuration, EspWifi};

use crate::display::Display;
use crate::state::{new_recording_id, State, Upload};
use crate::take_photo;

pub const WIFI_NAME: &str = env!("GLASSES_WIFI_NAME");
pub const WIFI_PASSWORD: &str = env!("GLASSES_WIFI_PASSWORD");
pub const SERVER: &str = env!("GLASSES_SERVER");
pub const TOKEN: &str = env!("GLASSES_TOKEN");
pub const UPLOAD_CHUNK: usize = 8192;
pub const PAUSED_CHECK: Duration = Duration::from_secs(3);

pub fn join_wifi(wifi: &mut BlockingWifi<EspWifi<'static>>) -> Result<(), EspError> {
    wifi.set_configuration(&Configuration::Client(ClientConfiguration {
        ssid: WIFI_NAME.try_into().unwrap_or_default(),
        password: WIFI_PASSWORD.try_into().unwrap_or_default(),
        auth_method: if WIFI_PASSWORD.is_empty() { AuthMethod::None } else { AuthMethod::WPA2Personal },
        ..Default::default()
    }))?;

    if !wifi.is_started()? {
        wifi.start()?;
    }

    wifi.connect()?;
    wifi.wait_netif_up()?;

    esp_idf_svc::sys::esp!(unsafe { esp_idf_svc::sys::esp_wifi_set_ps(esp_idf_svc::sys::wifi_ps_type_t_WIFI_PS_NONE) })
}

pub fn request(open: &mut Option<EspHttpConnection>, method: Method, url: &str, headers: &[(&str, &str)], body: &[u8],
           authorized: bool) -> Result<(u16, Vec<u8>), EspError> {
    let reply = exchange(open, method, url, headers, body, authorized);

    if reply.is_err() {
        *open = None;
    }

    reply
}

pub fn exchange(open: &mut Option<EspHttpConnection>, method: Method, url: &str, headers: &[(&str, &str)], body: &[u8],
            authorized: bool) -> Result<(u16, Vec<u8>), EspError> {
    if open.is_none() {
        *open = Some(EspHttpConnection::new(&HttpConfiguration {
            crt_bundle_attach: Some(esp_idf_svc::sys::esp_crt_bundle_attach),
            timeout: Some(Duration::from_secs(20)),
            buffer_size_tx: Some(UPLOAD_CHUNK),
            ..Default::default()
        })?);
    }

    let connection = open.as_mut().unwrap();

    let authorization = format!("Bearer {TOKEN}");
    let length = body.len().to_string();
    let mut all_headers = vec![("Content-Length", length.as_str())];

    if authorized {
        all_headers.push(("Authorization", authorization.as_str()));
    }

    all_headers.extend_from_slice(headers);

    connection.initiate_request(method, url, &all_headers)?;

    let mut written = 0;
    while written < body.len() {
        written += connection.write(&body[written..])?;
    }

    connection.initiate_response()?;
    let status = connection.status();

    let mut reply = Vec::new();
    let mut buffer = [0u8; 512];
    loop {
        let read = connection.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        reply.extend_from_slice(&buffer[..read]);
    }

    Ok((status, reply))
}

pub fn ask_server(open: &mut Option<EspHttpConnection>, method: Method, path: &str, headers: &[(&str, &str)], body: &[u8])
              -> Result<(u16, Vec<u8>), EspError> {
    request(open, method, &format!("{SERVER}{path}"), headers, body, true)
}

pub fn fetch_settings(open: &mut Option<EspHttpConnection>, recording: &str) -> Option<Vec<u8>> {
    let (status, reply) = ask_server(open, Method::Get, "/settings", &[("X-Recording", recording)], &[]).ok()?;

    (status == 200).then_some(reply)
}

pub fn send_frame(server: &mut Option<EspHttpConnection>, storage: &mut Option<EspHttpConnection>, recording: &str,
              jpeg: &[u8], upload: Option<Upload>) -> Result<(u16, Vec<u8>), EspError> {
    if let Some(upload) = upload {
        match request(storage, Method::Put, &upload.url, &[], jpeg, false) {
            Ok((200, _)) => {
                let headers = [("X-Recording", recording), ("X-Uploaded", upload.name.as_str())];

                return ask_server(server, Method::Post, "/glasses/frames", &headers, &[]);
            }
            Ok((status, _)) => println!("storage said {status}, sending to the server instead"),
            Err(error) => println!("storage upload failed: {error}, sending to the server instead"),
        }
    }

    let headers = [("X-Recording", recording), ("Content-Type", "image/jpeg")];

    ask_server(server, Method::Post, "/glasses/frames", &headers, jpeg)
}

pub fn timed_send(server: &mut Option<EspHttpConnection>, storage: &mut Option<EspHttpConnection>, recording: &str,
              jpeg: &[u8], upload: Option<Upload>) -> Result<(u16, Vec<u8>), EspError> {
    let started = Instant::now();
    let direct = upload.is_some();
    let fresh = if direct { storage.is_none() } else { server.is_none() };
    let result = send_frame(server, storage, recording, jpeg, upload);

    println!("sent {} bytes {} in {} ms{}", jpeg.len(), if direct { "to storage" } else { "to the server" },
             started.elapsed().as_millis(), if fresh { " (new connection)" } else { "" });

    result
}

pub struct Online {
    pub wifi: BlockingWifi<EspWifi<'static>>,
    pub joined: bool,
    pub server: Option<EspHttpConnection>,
    pub storage: Option<EspHttpConnection>,
    pub upload: Option<Upload>,
}

impl Online {
    pub fn stop(&mut self) {
        if self.joined || self.wifi.is_started().unwrap_or(false) {
            println!("stopping Wi-Fi for the phone");
            let _ = self.wifi.stop();
        }

        self.joined = false;
        self.server = None;
        self.storage = None;
        self.upload = None;
    }

    pub fn join(&mut self, state: &mut State, display: &mut Display) -> bool {
        if self.joined && self.wifi.is_connected().unwrap_or(false) {
            return true;
        }

        display.show(&["WiFi..."]);

        if let Err(error) = join_wifi(&mut self.wifi) {
            let seen = self.wifi.scan().unwrap_or_default();
            let found = seen.iter().any(|network| network.ssid.as_str() == WIFI_NAME);

            println!("joining {WIFI_NAME} failed: {error}");
            display.show(&["WiFi...", if found { "seen" } else { "no hotspot" }]);

            return false;
        }

        self.joined = true;

        if let Some(reply) = fetch_settings(&mut self.server, &state.recording_id) {
            self.upload = state.apply(&reply, display).flatten();
        }

        true
    }

    pub fn step(&mut self, state: &mut State, display: &mut Display) {
        if !state.recording || state.rate <= 0.0 {
            display.show(&[if state.recording { "No pics" } else { "Paused" }]);

            if let Some(ended) = state.stopped.take() {
                let _ = ask_server(&mut self.server, Method::Post, &format!("/glasses/recordings/{ended}/end"), &[], &[]);
            }

            thread::sleep(PAUSED_CHECK);

            let probe = if state.recording { state.recording_id.clone() } else { new_recording_id() };
            if let Some(reply) = fetch_settings(&mut self.server, &probe) {
                self.upload = state.apply(&reply, display).flatten();
            }

            return;
        }

        let started = Instant::now();
        let mut problem = String::new();

        match take_photo() {
            Some((jpeg, _, _)) => match timed_send(&mut self.server, &mut self.storage, &state.recording_id, &jpeg,
                                                   self.upload.take()) {
                Ok((200, reply)) => {
                    state.sent += 1;
                    self.upload = state.apply(&reply, display).flatten();
                }
                Ok((status, _)) => {
                    println!("server said {status}");
                    problem = format!("Err {status}");
                }
                Err(error) => {
                    println!("upload failed: {error}");
                    problem = "No net".to_string();

                    if !self.wifi.is_connected().unwrap_or(false) {
                        self.joined = false;
                    }
                }
            },
            None => problem = "No pic".to_string(),
        }

        display.show(&[&format!("Rec {}", state.sent), &problem]);

        if state.recording && state.rate > 0.0 {
            thread::sleep(state.gap().saturating_sub(started.elapsed()));
        }
    }
}
