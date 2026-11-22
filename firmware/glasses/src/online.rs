//! Wi-Fi mode: join the network and send photos to the server or straight to storage.

use std::thread;
use std::time::{Duration, Instant};
use esp_idf_svc::http::client::{Configuration as HttpConfiguration, EspHttpConnection};
use esp_idf_svc::http::Method;
use esp_idf_svc::sys::EspError;
use esp_idf_svc::wifi::{AuthMethod, BlockingWifi, ClientConfiguration, Configuration, EspWifi};

use crate::display::Display;
use crate::state::{new_recording_id, State, StorageUpload};
use crate::take_photo;

pub const WIFI_NAME: &str = env!("GLASSES_WIFI_NAME");
pub const WIFI_PASSWORD: &str = env!("GLASSES_WIFI_PASSWORD");
pub const SERVER: &str = env!("GLASSES_SERVER");
pub const TOKEN: &str = env!("GLASSES_TOKEN");
pub const HTTP_BUFFER_BYTES: usize = 8192;
pub const PAUSED_POLL: Duration = Duration::from_secs(3);

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

pub fn send_request(connection: &mut Option<EspHttpConnection>, method: Method, url: &str, headers: &[(&str, &str)], body: &[u8],
           authorized: bool) -> Result<(u16, Vec<u8>), EspError> {
    let reply = send_request_once(connection, method, url, headers, body, authorized);

    if reply.is_err() {
        *connection = None;
    }

    reply
}

pub fn send_request_once(connection: &mut Option<EspHttpConnection>, method: Method, url: &str, headers: &[(&str, &str)], body: &[u8],
            authorized: bool) -> Result<(u16, Vec<u8>), EspError> {
    if connection.is_none() {
        *connection = Some(EspHttpConnection::new(&HttpConfiguration {
            crt_bundle_attach: Some(esp_idf_svc::sys::esp_crt_bundle_attach),
            timeout: Some(Duration::from_secs(20)),
            buffer_size_tx: Some(HTTP_BUFFER_BYTES),
            ..Default::default()
        })?);
    }

    let active = connection.as_mut().unwrap();

    let authorization = format!("Bearer {TOKEN}");
    let length = body.len().to_string();
    let mut all_headers = vec![("Content-Length", length.as_str())];

    if authorized {
        all_headers.push(("Authorization", authorization.as_str()));
    }

    all_headers.extend_from_slice(headers);

    active.initiate_request(method, url, &all_headers)?;

    let mut written = 0;
    while written < body.len() {
        written += active.write(&body[written..])?;
    }

    active.initiate_response()?;
    let status = active.status();

    let mut reply = Vec::new();
    let mut buffer = [0u8; 512];
    loop {
        let read = active.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        reply.extend_from_slice(&buffer[..read]);
    }

    Ok((status, reply))
}

pub fn server_request(connection: &mut Option<EspHttpConnection>, method: Method, path: &str, headers: &[(&str, &str)], body: &[u8])
              -> Result<(u16, Vec<u8>), EspError> {
    send_request(connection, method, &format!("{SERVER}{path}"), headers, body, true)
}

pub fn fetch_settings(connection: &mut Option<EspHttpConnection>, recording_id: &str) -> Option<Vec<u8>> {
    let (status, reply) = server_request(connection, Method::Get, "/settings", &[("X-Recording", recording_id)], &[]).ok()?;

    (status == 200).then_some(reply)
}

pub fn upload_frame(server_connection: &mut Option<EspHttpConnection>,
                    storage_connection: &mut Option<EspHttpConnection>, recording_id: &str,
              jpeg: &[u8], upload: Option<StorageUpload>) -> Result<(u16, Vec<u8>), EspError> {
    if let Some(upload) = upload {
        match send_request(storage_connection, Method::Put, &upload.url, &[], jpeg, false) {
            Ok((200, _)) => {
                let headers = [("X-Recording", recording_id), ("X-Uploaded", upload.object_name.as_str())];

                return server_request(server_connection, Method::Post, "/glasses/frames", &headers, &[]);
            }
            Ok((status, _)) => println!("storage said {status}, sending to the server instead"),
            Err(error) => println!("storage upload failed: {error}, sending to the server instead"),
        }
    }

    let headers = [("X-Recording", recording_id), ("Content-Type", "image/jpeg")];

    server_request(server_connection, Method::Post, "/glasses/frames", &headers, jpeg)
}

pub fn upload_frame_logged(server_connection: &mut Option<EspHttpConnection>,
                    storage_connection: &mut Option<EspHttpConnection>, recording_id: &str,
              jpeg: &[u8], upload: Option<StorageUpload>) -> Result<(u16, Vec<u8>), EspError> {
    let started = Instant::now();
    let to_storage = upload.is_some();
    let new_connection = if to_storage { storage_connection.is_none() } else { server_connection.is_none() };
    let result = upload_frame(server_connection, storage_connection, recording_id, jpeg, upload);

    println!("sent {} bytes {} in {} ms{}", jpeg.len(), if to_storage { "to storage" } else { "to the server" },
             started.elapsed().as_millis(), if new_connection { " (new connection)" } else { "" });

    result
}

pub struct Online {
    pub wifi: BlockingWifi<EspWifi<'static>>,
    pub wifi_joined: bool,
    pub server_connection: Option<EspHttpConnection>,
    pub storage_connection: Option<EspHttpConnection>,
    pub upload: Option<StorageUpload>,
}

impl Online {
    pub fn stop(&mut self) {
        if self.wifi_joined || self.wifi.is_started().unwrap_or(false) {
            println!("stopping Wi-Fi for the phone");
            let _ = self.wifi.stop();
        }

        self.wifi_joined = false;
        self.server_connection = None;
        self.storage_connection = None;
        self.upload = None;
    }

    pub fn ensure_online(&mut self, state: &mut State, display: &mut Display) -> bool {
        if self.wifi_joined && self.wifi.is_connected().unwrap_or(false) {
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

        self.wifi_joined = true;

        if let Some(reply) = fetch_settings(&mut self.server_connection, &state.recording_id) {
            self.upload = state.apply_settings(&reply, display).flatten();
        }

        true
    }

    pub fn record_one_by_wifi(&mut self, state: &mut State, display: &mut Display) {
        if !state.recording || state.frames_per_minute <= 0.0 {
            display.show(&[if state.recording { "No pics" } else { "Paused" }]);

            if let Some(ended) = state.recording_to_end.take() {
                let _ = server_request(&mut self.server_connection, Method::Post, &format!("/glasses/recordings/{ended}/end"), &[], &[]);
            }

            thread::sleep(PAUSED_POLL);

            let settings_recording_id = if state.recording { state.recording_id.clone() } else { new_recording_id() };
            if let Some(reply) = fetch_settings(&mut self.server_connection, &settings_recording_id) {
                self.upload = state.apply_settings(&reply, display).flatten();
            }

            return;
        }

        let started = Instant::now();
        let mut problem = String::new();

        match take_photo() {
            Some((jpeg, _, _)) => match upload_frame_logged(&mut self.server_connection, &mut self.storage_connection, &state.recording_id, &jpeg,
                                                   self.upload.take()) {
                Ok((200, reply)) => {
                    state.frames_sent += 1;
                    self.upload = state.apply_settings(&reply, display).flatten();
                }
                Ok((status, _)) => {
                    println!("server said {status}");
                    problem = format!("Err {status}");
                }
                Err(error) => {
                    println!("upload failed: {error}");
                    problem = "No net".to_string();

                    if !self.wifi.is_connected().unwrap_or(false) {
                        self.wifi_joined = false;
                    }
                }
            },
            None => problem = "No pic".to_string(),
        }

        display.show(&[&format!("Rec {}", state.frames_sent), &problem]);

        if state.recording && state.frames_per_minute > 0.0 {
            thread::sleep(state.frame_interval().saturating_sub(started.elapsed()));
        }
    }
}
