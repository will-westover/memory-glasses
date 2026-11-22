use std::thread;
use std::time::{Duration, Instant};

use embedded_graphics::mono_font::ascii::{FONT_10X20, FONT_6X10};
use embedded_graphics::mono_font::MonoTextStyle;
use embedded_graphics::pixelcolor::BinaryColor;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::{PrimitiveStyle, Rectangle};
use embedded_graphics::text::Text;
use esp_idf_svc::hal::delay::TickType;
use esp_idf_svc::hal::gpio::{Gpio5, Gpio6, PinDriver, Pull};
use esp_idf_svc::hal::i2c::{I2cConfig, I2cDriver, I2C0};
use esp_idf_svc::hal::peripherals::Peripherals;
use esp_idf_svc::hal::units::FromValueType;
use ssd1306::mode::DisplayConfig;
use ssd1306::prelude::*;
use ssd1306::{I2CDisplayInterface, Ssd1306};

const DISPLAY_ADDRESSES: [u8; 2] = [0x3C, 0x3D];

fn scan_i2c_addresses(i2c: &mut I2cDriver) -> Vec<u8> {
    let timeout = TickType::new_millis(20).ticks();

    (1..127u8).filter(|&address| i2c.write(address, &[], timeout).is_ok()).collect()
}

fn level_name(high: bool) -> &'static str {
    if high { "high" } else { "LOW" }
}

fn find_display() -> Option<(I2cDriver<'static>, u8)> {
    {
        let sda = PinDriver::input(unsafe { Gpio5::steal() }, Pull::Up).unwrap();
        let scl = PinDriver::input(unsafe { Gpio6::steal() }, Pull::Up).unwrap();
        thread::sleep(Duration::from_millis(10));
        println!("Lines at rest (both should be high): SDA {}, SCL {}", level_name(sda.is_high()), level_name(scl.is_high()));
    }

    let config = I2cConfig::new().baudrate(400.kHz().into());
    let mut i2c = I2cDriver::new(unsafe { I2C0::steal() }, unsafe { Gpio5::steal() }, unsafe { Gpio6::steal() }, &config).unwrap();

    let found = scan_i2c_addresses(&mut i2c);
    println!("I2C devices on D4 (SDA) and D5 (SCL): {found:02X?}");

    found.into_iter().find(|address| DISPLAY_ADDRESSES.contains(address)).map(|address| (i2c, address))
}

fn main() {
    esp_idf_svc::sys::link_patches();
    esp_idf_svc::log::EspLogger::initialize_default();

    let peripherals = Peripherals::take().unwrap();
    let mut led = PinDriver::output(peripherals.pins.gpio21).unwrap();

    let (i2c, address) = loop {
        if let Some(found) = find_display() {
            break found;
        }

        println!("No display at 0x3C or 0x3D yet; check VCC, GND, SDA and SCL against hardware/WIRING.md");
        led.toggle().unwrap();
        thread::sleep(Duration::from_secs(1));
    };

    let interface = I2CDisplayInterface::new_custom_address(i2c, address);
    let mut screen = Ssd1306::new(interface, DisplaySize128x64, DisplayRotation::Rotate0).into_buffered_graphics_mode();
    screen.init().unwrap();
    println!("OK: display found at 0x{address:02X}");

    screen.clear(BinaryColor::On).unwrap();
    screen.flush().unwrap();
    thread::sleep(Duration::from_millis(1500));

    let border = Rectangle::new(Point::zero(), Size::new(128, 64)).into_styled(PrimitiveStyle::with_stroke(BinaryColor::On, 1));
    screen.clear(BinaryColor::Off).unwrap();
    border.draw(&mut screen).unwrap();
    screen.flush().unwrap();
    thread::sleep(Duration::from_millis(1500));

    let small = MonoTextStyle::new(&FONT_6X10, BinaryColor::On);
    let large = MonoTextStyle::new(&FONT_10X20, BinaryColor::On);
    let started = Instant::now();

    loop {
        let seconds = started.elapsed().as_secs();

        screen.clear(BinaryColor::Off).unwrap();
        Text::new("MEMORY GLASSES", Point::new(4, 12), small).draw(&mut screen).unwrap();
        Text::new("Display OK", Point::new(4, 24), small).draw(&mut screen).unwrap();
        Text::new(&format!("{seconds} s"), Point::new(4, 52), large).draw(&mut screen).unwrap();
        screen.flush().unwrap();

        println!("Running: {seconds} s");

        if seconds % 2 == 0 { led.set_low().unwrap() } else { led.set_high().unwrap() }
        thread::sleep(Duration::from_secs(1));
    }
}
