use std::thread;
use std::time::{Duration, Instant};

use camera_test::{start_camera, take_photo};
use embedded_graphics::mono_font::ascii::FONT_6X10;
use embedded_graphics::mono_font::MonoTextStyle;
use embedded_graphics::pixelcolor::BinaryColor;
use embedded_graphics::prelude::*;
use embedded_graphics::text::Text;
use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::hal::delay::TickType;
use esp_idf_svc::hal::i2c::{I2cConfig, I2cDriver};
use esp_idf_svc::hal::peripherals::Peripherals;
use esp_idf_svc::hal::units::FromValueType;
use esp_idf_svc::http::client::{Configuration as HttpConfiguration, EspHttpConnection};
use esp_idf_svc::http::Method;
use esp_idf_svc::nvs::EspDefaultNvsPartition;
use esp_idf_svc::sys::EspError;
use esp_idf_svc::wifi::{AuthMethod, BlockingWifi, ClientConfiguration, Configuration, EspWifi};
use ssd1306::mode::{BufferedGraphicsMode, DisplayConfig};
use ssd1306::prelude::*;
use ssd1306::{I2CDisplayInterface, Ssd1306};

const WIFI_NAME: &str = env!("GLASSES_WIFI_NAME");
const WIFI_PASSWORD: &str = env!("GLASSES_WIFI_PASSWORD");
const SERVER: &str = env!("GLASSES_SERVER");
const TOKEN: &str = env!("GLASSES_TOKEN");

const DISPLAY_ADDRESSES: [u8; 2] = [0x3C, 0x3D];
const DEFAULT_FRAMES_PER_MINUTE: f64 = 30.0;
const SETTINGS_EVERY_FRAMES: u32 = 10;
const UPLOAD_CHUNK: usize = 8192;
const SOUND_ONLY_WAIT: Duration = Duration::from_secs(30);

type Screen = Ssd1306<I2CInterface<I2cDriver<'static>>, DisplaySize128x64, BufferedGraphicsMode<DisplaySize128x64>>;

fn start_screen(mut i2c: I2cDriver<'static>) -> Option<Screen> {
    let timeout = TickType::new_millis(20).ticks();
    let address = DISPLAY_ADDRESSES.into_iter().find(|&address| i2c.write(address, &[], timeout).is_ok())?;

    let interface = I2CDisplayInterface::new_custom_address(i2c, address);
    let mut screen = Ssd1306::new(interface, DisplaySize128x64, DisplayRotation::Rotate0).into_buffered_graphics_mode();

    screen.init().ok().map(|_| screen)
}

fn show(screen: &mut Option<Screen>, lines: &[&str]) {
    println!("{}", lines.join(" | "));

    let Some(screen) = screen else { return };
    let style = MonoTextStyle::new(&FONT_6X10, BinaryColor::On);

    let _ = screen.clear(BinaryColor::Off);

    for (row, line) in lines.iter().enumerate() {
        let _ = Text::new(line, Point::new(2, 10 + 12 * row as i32), style).draw(screen);
    }

    let _ = screen.flush();
}

fn join_wifi(wifi: &mut BlockingWifi<EspWifi<'static>>) -> Result<(), EspError> {
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

fn request(open: &mut Option<EspHttpConnection>, method: Method, path: &str, headers: &[(&str, &str)], body: &[u8])
           -> Result<(u16, Vec<u8>), EspError> {
    let reply = exchange(open, method, path, headers, body);

    if reply.is_err() {
        *open = None;
    }

    reply
}

fn exchange(open: &mut Option<EspHttpConnection>, method: Method, path: &str, headers: &[(&str, &str)], body: &[u8])
            -> Result<(u16, Vec<u8>), EspError> {
    if open.is_none() {
        *open = Some(EspHttpConnection::new(&HttpConfiguration {
            crt_bundle_attach: Some(esp_idf_svc::sys::esp_crt_bundle_attach),
            timeout: Some(Duration::from_secs(20)),
            buffer_size_tx: Some(UPLOAD_CHUNK),
            ..Default::default()
        })?);
    }

    let connection = open.as_mut().unwrap();

    let url = format!("{SERVER}{path}");
    let authorization = format!("Bearer {TOKEN}");
    let length = body.len().to_string();
    let mut all_headers = vec![("Authorization", authorization.as_str()), ("Content-Length", length.as_str())];
    all_headers.extend_from_slice(headers);

    connection.initiate_request(method, &url, &all_headers)?;

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

fn frames_per_minute(open: &mut Option<EspHttpConnection>) -> Option<f64> {
    let (status, reply) = request(open, Method::Get, "/settings", &[], &[]).ok()?;

    if status != 200 {
        return None;
    }

    serde_json::from_slice::<serde_json::Value>(&reply).ok()?["frames_per_minute"].as_f64()
}

fn send_frame(open: &mut Option<EspHttpConnection>, recording: &str, jpeg: &[u8]) -> Result<u16, EspError> {
    let headers = [("X-Recording", recording), ("Content-Type", "image/jpeg")];

    request(open, Method::Post, "/glasses/frames", &headers, jpeg).map(|(status, _)| status)
}

fn timed_send(open: &mut Option<EspHttpConnection>, recording: &str, jpeg: &[u8]) -> Result<u16, EspError> {
    let started = Instant::now();
    let fresh = open.is_none();
    let result = send_frame(open, recording, jpeg);

    println!("sent {} bytes in {} ms{}", jpeg.len(), started.elapsed().as_millis(), if fresh { " (new connection)" } else { "" });

    result
}

fn main() {
    esp_idf_svc::sys::link_patches();
    esp_idf_svc::log::EspLogger::initialize_default();

    let peripherals = Peripherals::take().unwrap();
    let config = I2cConfig::new().baudrate(400.kHz().into());
    let i2c = I2cDriver::new(peripherals.i2c0, peripherals.pins.gpio5, peripherals.pins.gpio6, &config).unwrap();
    let mut screen = start_screen(i2c);

    show(&mut screen, &["MEMORY GLASSES", "", "Starting camera"]);
    if let Err(error) = start_camera(camera_test::STREAM) {
        show(&mut screen, &["MEMORY GLASSES", "", "Camera failed", &format!("error {error}")]);
        loop {
            thread::sleep(Duration::from_secs(60));
        }
    }

    if WIFI_NAME.is_empty() || SERVER.is_empty() {
        show(&mut screen, &["MEMORY GLASSES", "", "No Wi-Fi set", "Fill in glasses.toml"]);
        loop {
            thread::sleep(Duration::from_secs(60));
        }
    }

    let sysloop = EspSystemEventLoop::take().unwrap();
    let nvs = EspDefaultNvsPartition::take().ok();
    let mut wifi = BlockingWifi::wrap(EspWifi::new(peripherals.modem, sysloop.clone(), nvs).unwrap(), sysloop).unwrap();

    while let Err(error) = join_wifi(&mut wifi) {
        let seen = wifi.scan().unwrap_or_default();
        let found = seen.iter().find(|network| network.ssid.as_str() == WIFI_NAME);

        for network in &seen {
            println!("  sees \"{}\" signal {} channel {}", network.ssid, network.signal_strength, network.channel);
        }

        let status = match found {
            Some(network) => format!("seen, signal {}", network.signal_strength),
            None => format!("not seen ({} others)", seen.len()),
        };

        show(&mut screen, &["MEMORY GLASSES", "", "Joining Wi-Fi", WIFI_NAME, &status, &format!("{error}")]);
        thread::sleep(Duration::from_secs(3));
    }

    let mut server = None;
    let recording = unsafe { format!("{:08x}{:08x}", esp_idf_svc::sys::esp_random(), esp_idf_svc::sys::esp_random()) };
    let mut rate = frames_per_minute(&mut server).unwrap_or(DEFAULT_FRAMES_PER_MINUTE);
    let (mut sent, mut failed) = (0u32, 0u32);
    let mut problem = String::new();

    show(&mut screen, &["MEMORY GLASSES", "", "Connected", &format!("{rate:.0} pictures a minute")]);

    loop {
        if rate <= 0.0 {
            show(&mut screen, &["MEMORY GLASSES", "", "Sound only", "No pictures"]);
            thread::sleep(SOUND_ONLY_WAIT);
            rate = frames_per_minute(&mut server).unwrap_or(rate);
            continue;
        }

        let started = Instant::now();

        match take_photo() {
            Some((jpeg, _, _)) => match timed_send(&mut server, &recording, &jpeg) {
                Ok(200) => sent += 1,
                Ok(status) => {
                    failed += 1;
                    problem = format!("server said {status}");
                }
                Err(error) => {
                    failed += 1;
                    problem = format!("{error}");

                    if !wifi.is_connected().unwrap_or(false) {
                        let _ = join_wifi(&mut wifi);
                    }
                }
            },
            None => {
                failed += 1;
                problem = "no picture".to_string();
            }
        }

        let failures = if failed > 0 { format!("Failed {failed}") } else { String::new() };
        show(&mut screen, &["MEMORY GLASSES", "", &format!("Sent {sent}"), &failures, &problem]);

        if (sent + failed) % SETTINGS_EVERY_FRAMES == 0 {
            rate = frames_per_minute(&mut server).unwrap_or(rate);
        }

        let gap = Duration::from_secs_f64(60.0 / rate);
        thread::sleep(gap.saturating_sub(started.elapsed()));
    }
}
