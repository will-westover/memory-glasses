use std::thread;
use std::time::{Duration, Instant};
use glasses::link::{mark_firmware_valid, Link, PhoneMessage};
use glasses::{start_camera, take_photo};
use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::hal::i2c::{I2cConfig, I2cDriver};
use esp_idf_svc::hal::peripherals::Peripherals;
use esp_idf_svc::hal::units::FromValueType;
use esp_idf_svc::nvs::EspDefaultNvsPartition;
use esp_idf_svc::wifi::{BlockingWifi, EspWifi};
use glasses::display::{start_screen, Display};
use glasses::online::{Online, PAUSED_POLL, SERVER, TOKEN, WIFI_NAME};
use glasses::state::State;

const VERSION: &str = env!("GLASSES_VERSION");
const ACK_TIMEOUT: Duration = Duration::from_secs(10);
const PHONE_RECONNECT_GRACE: Duration = Duration::from_secs(30);
const MESSAGE_POLL: Duration = Duration::from_millis(200);

fn handle_phone_message(link: &Link, state: &mut State, display: &mut Display, wait: Duration) -> Option<u32> {
    match link.next_message(wait)? {
        PhoneMessage::Settings(json) => {
            state.apply_settings(&json, display);
            None
        }
        PhoneMessage::Ack(number) => Some(number),
    }
}

fn wait_for_ack(link: &Link, state: &mut State, display: &mut Display, number: u32) -> bool {
    let deadline = Instant::now() + ACK_TIMEOUT;

    while Instant::now() < deadline && link.phone_ready() {
        if handle_phone_message(link, state, display, MESSAGE_POLL).is_some_and(|acked| acked >= number) {
            return true;
        }
    }

    false
}

fn record_one_by_bluetooth(link: &Link, state: &mut State, display: &mut Display) {
    if !state.recording || state.frames_per_minute <= 0.0 {
        display.show(&["BT", if state.recording { "No pics" } else { "Paused" }]);
        state.recording_to_end = None;
        handle_phone_message(link, state, display, MESSAGE_POLL);
        return;
    }

    let started = Instant::now();

    match take_photo() {
        Some((jpeg, _, _)) => {
            let number = state.frames_sent + 1;
            let send_started = Instant::now();

            if link.send_frame(&jpeg, number, &state.recording_id) && wait_for_ack(link, state, display, number) {
                state.frames_sent = number;
                println!("sent frame {number}: {} bytes by Bluetooth in {} ms", jpeg.len(),
                         send_started.elapsed().as_millis());
                display.show(&["BT", &state.status_line()]);
            } else {
                println!("frame {number} wasn't acknowledged");
                display.show(&["BT", &state.status_line(), "No ack"]);
            }
        }
        None => display.show(&["BT", &state.status_line(), "No pic"]),
    }

    while state.recording && state.frames_per_minute > 0.0 && started.elapsed() < state.frame_interval() && link.phone_ready() {
        handle_phone_message(link, state, display, MESSAGE_POLL.min(state.frame_interval().saturating_sub(started.elapsed())));
    }
}

fn main() {
    esp_idf_svc::sys::link_patches();
    esp_idf_svc::log::EspLogger::initialize_default();

    let peripherals = Peripherals::take().unwrap();
    let config = I2cConfig::new().baudrate(400.kHz().into());
    let i2c = I2cDriver::new(peripherals.i2c0, peripherals.pins.gpio5, peripherals.pins.gpio6, &config).unwrap();
    let mut display = Display::new(start_screen(i2c));

    println!("firmware {VERSION}");
    display.show(&["Starting"]);

    if let Err(error) = start_camera(glasses::STREAM_FRAME_SIZE) {
        println!("camera failed to start: error {error}");
        display.show(&["Cam err", &format!("{error}")]);
        loop {
            thread::sleep(Duration::from_secs(60));
        }
    }

    let sysloop = EspSystemEventLoop::take().unwrap();
    let nvs = EspDefaultNvsPartition::take().ok();
    let link = Link::start(VERSION, TOKEN);
    let mut online = (!WIFI_NAME.is_empty() && !SERVER.is_empty()).then(|| Online {
        wifi: BlockingWifi::wrap(EspWifi::new(peripherals.modem, sysloop.clone(), nvs).unwrap(), sysloop).unwrap(),
        wifi_joined: false,
        server_connection: None,
        storage_connection: None,
        upload: None,
    });

    let mut state = State::new();
    let mut phone_last_seen = Instant::now();
    let mut firmware_marked_valid = false;

    display.show(&["BT", "waiting"]);

    loop {
        if let Some(percent) = link.update_progress() {
            display.show(&["Updating", &format!("{percent}%")]);
            thread::sleep(Duration::from_millis(300));
            continue;
        }

        if link.phone_ready() {
            if !firmware_marked_valid {
                mark_firmware_valid();
                firmware_marked_valid = true;
            }

            if let Some(online) = online.as_mut() {
                online.stop();
            }

            phone_last_seen = Instant::now();
            record_one_by_bluetooth(&link, &mut state, &mut display);
            continue;
        }

        match online.as_mut() {
            Some(online) if phone_last_seen.elapsed() > PHONE_RECONNECT_GRACE => {
                if online.ensure_online(&mut state, &mut display) {
                    online.record_one_by_wifi(&mut state, &mut display);
                } else {
                    thread::sleep(PAUSED_POLL);
                }
            }
            _ => {
                display.show(&["BT", "waiting"]);
                handle_phone_message(&link, &mut state, &mut display, Duration::from_millis(500));
            }
        }
    }
}
