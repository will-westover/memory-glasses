use std::thread;
use std::time::{Duration, Instant};

use glasses::{start_camera, take_photo};
use embedded_graphics::mono_font::ascii::{FONT_10X20, FONT_6X10};
use embedded_graphics::mono_font::MonoTextStyle;
use embedded_graphics::pixelcolor::BinaryColor;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::{Line, PrimitiveStyle, Rectangle};
use embedded_graphics::text::{Baseline, Text};
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
const OUTLINE_FOR: Duration = Duration::from_secs(6);
const UPLOAD_CHUNK: usize = 8192;
const PAUSED_CHECK: Duration = Duration::from_secs(3);

type Screen = Ssd1306<I2CInterface<I2cDriver<'static>>, DisplaySize128x64, BufferedGraphicsMode<DisplaySize128x64>>;

fn start_screen(mut i2c: I2cDriver<'static>) -> Option<Screen> {
    let timeout = TickType::new_millis(20).ticks();
    let address = DISPLAY_ADDRESSES.into_iter().find(|&address| i2c.write(address, &[], timeout).is_ok())?;

    let interface = I2CDisplayInterface::new_custom_address(i2c, address);
    let mut screen = Ssd1306::new(interface, DisplaySize128x64, DisplayRotation::Rotate0).into_buffered_graphics_mode();

    screen.init().ok().map(|_| screen)
}

#[derive(Clone, PartialEq)]
struct Look {
    scale: f64,
    x: f64,
    y: f64,
    brightness: f64,
    large_text: bool,
    mirror: bool,
    flip: bool,
    pattern: bool,
}

impl Default for Look {
    fn default() -> Self {
        Look { scale: 1.0, x: 0.0, y: 0.0, brightness: 1.0, large_text: false, mirror: false, flip: false, pattern: false }
    }
}

struct Settings {
    frames_per_minute: f64,
    recording: bool,
    look: Look,
}

fn parse_settings(reply: &[u8]) -> Option<Settings> {
    let json = serde_json::from_slice::<serde_json::Value>(reply).ok()?;
    let base = Look::default();
    let number = |name: &str, default: f64| json[name].as_f64().unwrap_or(default);
    let flag = |name: &str, default: bool| json[name].as_bool().unwrap_or(default);

    Some(Settings {
        frames_per_minute: json["frames_per_minute"].as_f64()?,
        recording: flag("recording", true),
        look: Look {
            scale: number("display_scale", base.scale),
            x: number("display_x", base.x),
            y: number("display_y", base.y),
            brightness: number("display_brightness", base.brightness),
            large_text: flag("display_large_text", base.large_text),
            mirror: flag("display_mirror", base.mirror),
            flip: flag("display_flip", base.flip),
            pattern: flag("display_pattern", base.pattern),
        },
    })
}

struct Display {
    screen: Option<Screen>,
    look: Look,
    outline_until: Option<Instant>,
    lines: Vec<String>,
}

impl Display {
    fn new(screen: Option<Screen>) -> Self {
        let mut display = Display { screen, look: Look::default(), outline_until: None, lines: Vec::new() };
        display.apply_to_screen();

        display
    }

    fn apply_to_screen(&mut self) {
        let Some(screen) = self.screen.as_mut() else { return };
        let rotation = if self.look.flip { DisplayRotation::Rotate180 } else { DisplayRotation::Rotate0 };
        let contrast = (self.look.brightness * 255.0).round().clamp(0.0, 255.0) as u8;

        let _ = screen.set_rotation(rotation);
        let _ = screen.set_mirror(self.look.mirror != self.look.flip);
        let _ = screen.set_brightness(Brightness::custom(2, contrast));
    }

    fn set_look(&mut self, look: Look) {
        if look == self.look {
            return;
        }

        let old = std::mem::replace(&mut self.look, look);
        let now = &self.look;

        if (old.mirror, old.flip, old.brightness) != (now.mirror, now.flip, now.brightness) {
            self.apply_to_screen();
        }

        let now = &self.look;
        if (old.scale, old.x, old.y, old.large_text) != (now.scale, now.x, now.y, now.large_text) {
            self.outline_until = Some(Instant::now() + OUTLINE_FOR);
        }

        self.draw();
    }

    fn show(&mut self, lines: &[&str]) {
        println!("{}", lines.join(" | "));
        self.lines = lines.iter().map(|line| line.to_string()).collect();
        self.draw();
    }

    fn area(&self) -> (Point, i32, i32) {
        let (width, height) = ((128.0 * self.look.scale).round() as i32, (64.0 * self.look.scale).round() as i32);
        let x = (128 - width) / 2 + ((128 - width) as f64 / 2.0 * self.look.x).round() as i32;
        let y = (64 - height) / 2 + ((64 - height) as f64 / 2.0 * self.look.y).round() as i32;

        (Point::new(x, y), width, height)
    }

    fn draw(&mut self) {
        let (corner, width, height) = self.area();
        let outlined = self.look.pattern || self.outline_until.is_some_and(|until| Instant::now() < until);
        let (font, line_height, char_width) = if self.look.large_text { (&FONT_10X20, 20, 10) } else { (&FONT_6X10, 11, 6) };
        let style = MonoTextStyle::new(font, BinaryColor::On);
        let stroke = PrimitiveStyle::with_stroke(BinaryColor::On, 1);

        let mut shown: Vec<String> = self.lines.iter().filter(|line| !line.is_empty()).cloned().collect();
        if self.look.pattern {
            shown = vec!["Aa 0123".to_string()];
        } else if outlined {
            shown.insert(0, format!("{:.0}%", self.look.scale * 100.0));
        }

        let Some(screen) = self.screen.as_mut() else { return };
        let _ = screen.clear(BinaryColor::Off);

        if outlined {
            let _ = Rectangle::new(corner, Size::new(width as u32, height as u32)).into_styled(stroke).draw(screen);
        }

        if self.look.pattern {
            let middle = corner + Point::new(width / 2, height / 2);
            let _ = Line::new(Point::new(corner.x, middle.y), Point::new(corner.x + width - 1, middle.y)).into_styled(stroke).draw(screen);
            let _ = Line::new(Point::new(middle.x, corner.y), Point::new(middle.x, corner.y + height - 1)).into_styled(stroke).draw(screen);

            for step in (2..width / 2 - 2).step_by(2) {
                let x = corner.x + step;
                let _ = Line::new(Point::new(x, corner.y + 2), Point::new(x, middle.y - 2)).into_styled(stroke).draw(screen);
            }

            for step in (2..height / 2 - 2).step_by(2) {
                let y = middle.y + step;
                let _ = Line::new(Point::new(middle.x + 2, y), Point::new(corner.x + width - 3, y)).into_styled(stroke).draw(screen);
            }
        }

        let inset = if outlined { 2 } else { 0 };
        let rows = ((height - 2 * inset) / line_height).max(1) as usize;
        let columns = ((width - 2 * inset) / char_width).max(1) as usize;
        let text_corner = if self.look.pattern { corner + Point::new(width / 2 + 2, 2) } else { corner + Point::new(inset, inset) };
        let columns = if self.look.pattern { columns / 2 } else { columns };

        for (row, line) in shown.iter().take(rows).enumerate() {
            let text: String = line.chars().take(columns).collect();
            let at = text_corner + Point::new(0, line_height * row as i32);
            let _ = Text::with_baseline(&text, at, style, Baseline::Top).draw(screen);
        }

        let _ = screen.flush();
    }
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

fn fetch_settings(open: &mut Option<EspHttpConnection>) -> Option<Settings> {
    let (status, reply) = request(open, Method::Get, "/settings", &[], &[]).ok()?;

    if status != 200 {
        return None;
    }

    parse_settings(&reply)
}

fn send_frame(open: &mut Option<EspHttpConnection>, recording: &str, jpeg: &[u8])
              -> Result<(u16, Option<Settings>), EspError> {
    let headers = [("X-Recording", recording), ("Content-Type", "image/jpeg")];

    request(open, Method::Post, "/glasses/frames", &headers, jpeg).map(|(status, reply)| (status, parse_settings(&reply)))
}

fn timed_send(open: &mut Option<EspHttpConnection>, recording: &str, jpeg: &[u8])
              -> Result<(u16, Option<Settings>), EspError> {
    let started = Instant::now();
    let fresh = open.is_none();
    let result = send_frame(open, recording, jpeg);

    println!("sent {} bytes in {} ms{}", jpeg.len(), started.elapsed().as_millis(), if fresh { " (new connection)" } else { "" });

    result
}

fn new_recording_id() -> String {
    unsafe { format!("{:08x}{:08x}", esp_idf_svc::sys::esp_random(), esp_idf_svc::sys::esp_random()) }
}

fn main() {
    esp_idf_svc::sys::link_patches();
    esp_idf_svc::log::EspLogger::initialize_default();

    let peripherals = Peripherals::take().unwrap();
    let config = I2cConfig::new().baudrate(400.kHz().into());
    let i2c = I2cDriver::new(peripherals.i2c0, peripherals.pins.gpio5, peripherals.pins.gpio6, &config).unwrap();
    let mut display = Display::new(start_screen(i2c));

    display.show(&["Starting"]);
    if let Err(error) = start_camera(glasses::STREAM) {
        println!("camera failed to start: error {error}");
        display.show(&["Cam err", &format!("{error}")]);
        loop {
            thread::sleep(Duration::from_secs(60));
        }
    }

    if WIFI_NAME.is_empty() || SERVER.is_empty() {
        println!("no Wi-Fi or server in glasses.toml");
        display.show(&["No WiFi", "setup"]);
        loop {
            thread::sleep(Duration::from_secs(60));
        }
    }

    let sysloop = EspSystemEventLoop::take().unwrap();
    let nvs = EspDefaultNvsPartition::take().ok();
    let mut wifi = BlockingWifi::wrap(EspWifi::new(peripherals.modem, sysloop.clone(), nvs).unwrap(), sysloop).unwrap();

    display.show(&["WiFi..."]);
    while let Err(error) = join_wifi(&mut wifi) {
        let seen = wifi.scan().unwrap_or_default();
        let found = seen.iter().find(|network| network.ssid.as_str() == WIFI_NAME);

        for network in &seen {
            println!("  sees \"{}\" signal {} channel {}", network.ssid, network.signal_strength, network.channel);
        }

        println!("joining {WIFI_NAME} failed: {error}");
        display.show(&["WiFi...", if found.is_some() { "seen" } else { "no hotspot" }]);
        thread::sleep(Duration::from_secs(3));
    }

    let mut server = None;
    let mut recording_id = new_recording_id();
    let (mut rate, mut recording) = (DEFAULT_FRAMES_PER_MINUTE, true);

    if let Some(settings) = fetch_settings(&mut server) {
        (rate, recording) = (settings.frames_per_minute, settings.recording);
        display.set_look(settings.look);
        display.outline_until = None;
    }

    let mut sent = 0u32;
    let mut problem = String::new();

    display.show(&["Ready"]);

    loop {
        if !recording || rate <= 0.0 {
            display.show(&[if recording { "No pics" } else { "Paused" }]);
            thread::sleep(PAUSED_CHECK);

            if let Some(settings) = fetch_settings(&mut server) {
                if !recording && settings.recording {
                    recording_id = new_recording_id();
                    sent = 0;
                }

                (rate, recording) = (settings.frames_per_minute, settings.recording);
                display.set_look(settings.look);
            }

            continue;
        }

        let started = Instant::now();

        match take_photo() {
            Some((jpeg, _, _)) => match timed_send(&mut server, &recording_id, &jpeg) {
                Ok((200, settings)) => {
                    sent += 1;
                    problem.clear();

                    if let Some(settings) = settings {
                        (rate, recording) = (settings.frames_per_minute, settings.recording);
                        display.set_look(settings.look);
                    }
                }
                Ok((status, _)) => {
                    println!("server said {status}");
                    problem = format!("Err {status}");
                }
                Err(error) => {
                    println!("upload failed: {error}");
                    problem = "No net".to_string();

                    if !wifi.is_connected().unwrap_or(false) {
                        let _ = join_wifi(&mut wifi);
                    }
                }
            },
            None => problem = "No pic".to_string(),
        }

        display.show(&[&format!("Rec {sent}"), &problem]);

        if !recording || rate <= 0.0 {
            continue;
        }

        let gap = Duration::from_secs_f64(60.0 / rate);
        thread::sleep(gap.saturating_sub(started.elapsed()));
    }
}
