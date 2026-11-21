use std::thread;
use std::time::{Duration, Instant};
use glasses::link::{keep_this_firmware, Link, Message};
use glasses::{start_camera, take_photo};
use esp_idf_svc::eventloop::EspSystemEventLoop;
use esp_idf_svc::hal::i2c::{I2cConfig, I2cDriver};
use esp_idf_svc::hal::peripherals::Peripherals;
use esp_idf_svc::hal::units::FromValueType;
use esp_idf_svc::nvs::EspDefaultNvsPartition;
use esp_idf_svc::wifi::{BlockingWifi, EspWifi};
use glasses::display::{start_screen, Display};
use glasses::online::{Online, PAUSED_CHECK, SERVER, TOKEN, WIFI_NAME};
use glasses::state::State;

const VERSION: &str = env!("GLASSES_VERSION");
const ACK_WAIT: Duration = Duration::from_secs(10);
const PHONE_GRACE: Duration = Duration::from_secs(30);
const LISTEN: Duration = Duration::from_millis(200);

fn listen(link: &Link, state: &mut State, display: &mut Display, wait: Duration) -> Option<u32> {
    match link.next_message(wait)? {
        Message::Settings(json) => {
            state.apply(&json, display);
            None
        }
        Message::Ack(number) => Some(number),
    }
}

fn wait_for_ack(link: &Link, state: &mut State, display: &mut Display, number: u32) -> bool {
    let deadline = Instant::now() + ACK_WAIT;

    while Instant::now() < deadline && link.ready() {
        if listen(link, state, display, LISTEN).is_some_and(|acked| acked >= number) {
            return true;
        }
    }

    false
}

fn by_phone(link: &Link, state: &mut State, display: &mut Display) {
    if !state.recording || state.rate <= 0.0 {
        display.show(&["BT", if state.recording { "No pics" } else { "Paused" }]);
        state.stopped = None;
        listen(link, state, display, LISTEN);
        return;
    }

    let started = Instant::now();

    match take_photo() {
        Some((jpeg, _, _)) => {
            let number = state.sent + 1;
            let sending = Instant::now();

            if link.send_frame(&jpeg, number, &state.recording_id) && wait_for_ack(link, state, display, number) {
                state.sent = number;
                println!("sent frame {number}: {} bytes by Bluetooth in {} ms", jpeg.len(),
                         sending.elapsed().as_millis());
                display.show(&["BT", &state.line()]);
            } else {
                println!("frame {number} wasn't acknowledged");
                display.show(&["BT", &state.line(), "No ack"]);
            }
        }
        None => display.show(&["BT", &state.line(), "No pic"]),
    }

    while state.recording && state.rate > 0.0 && started.elapsed() < state.gap() && link.ready() {
        listen(link, state, display, LISTEN.min(state.gap().saturating_sub(started.elapsed())));
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

    if let Err(error) = start_camera(glasses::STREAM) {
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
        joined: false,
        server: None,
        storage: None,
        upload: None,
    });

    let mut state = State::new();
    let mut last_phone = Instant::now();
    let mut kept = false;

    display.show(&["BT", "waiting"]);

    loop {
        if let Some(percent) = link.updating() {
            display.show(&["Updating", &format!("{percent}%")]);
            thread::sleep(Duration::from_millis(300));
            continue;
        }

        if link.ready() {
            if !kept {
                keep_this_firmware();
                kept = true;
            }

            if let Some(online) = online.as_mut() {
                online.stop();
            }

            last_phone = Instant::now();
            by_phone(&link, &mut state, &mut display);
            continue;
        }

        match online.as_mut() {
            Some(online) if last_phone.elapsed() > PHONE_GRACE => {
                if online.join(&mut state, &mut display) {
                    online.step(&mut state, &mut display);
                } else {
                    thread::sleep(PAUSED_CHECK);
                }
            }
            _ => {
                display.show(&["BT", "waiting"]);
                listen(&link, &mut state, &mut display, Duration::from_millis(500));
            }
        }
    }
}
