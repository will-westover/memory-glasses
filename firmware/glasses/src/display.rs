//! The small display: where text is drawn, how it looks, and the focus pattern.

use std::time::{Duration, Instant};
use embedded_graphics::mono_font::ascii::{FONT_10X20, FONT_6X10};
use embedded_graphics::mono_font::MonoTextStyle;
use embedded_graphics::pixelcolor::BinaryColor;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::{Line, PrimitiveStyle, Rectangle};
use embedded_graphics::text::{Baseline, Text};
use esp_idf_svc::hal::delay::TickType;
use esp_idf_svc::hal::i2c::I2cDriver;
use ssd1306::mode::{BufferedGraphicsMode, DisplayConfig};
use ssd1306::prelude::*;
use ssd1306::{I2CDisplayInterface, Ssd1306};

pub const DISPLAY_ADDRESSES: [u8; 2] = [0x3C, 0x3D];
pub const OUTLINE_FOR: Duration = Duration::from_secs(6);

pub type Screen = Ssd1306<I2CInterface<I2cDriver<'static>>, DisplaySize128x64, BufferedGraphicsMode<DisplaySize128x64>>;

pub fn start_screen(mut i2c: I2cDriver<'static>) -> Option<Screen> {
    let timeout = TickType::new_millis(20).ticks();
    let address = DISPLAY_ADDRESSES.into_iter().find(|&address| i2c.write(address, &[], timeout).is_ok())?;

    let interface = I2CDisplayInterface::new_custom_address(i2c, address);
    let mut screen = Ssd1306::new(interface, DisplaySize128x64, DisplayRotation::Rotate0).into_buffered_graphics_mode();

    screen.init().ok().map(|_| screen)
}

#[derive(Clone, PartialEq)]
pub struct Look {
    pub scale: f64,
    pub x: f64,
    pub y: f64,
    pub brightness: f64,
    pub large_text: bool,
    pub mirror: bool,
    pub flip: bool,
    pub pattern: bool,
}

impl Default for Look {
    fn default() -> Self {
        Look { scale: 1.0, x: 0.0, y: 0.0, brightness: 1.0, large_text: false, mirror: false, flip: false, pattern: false }
    }
}

pub struct Display {
    pub screen: Option<Screen>,
    pub look: Look,
    pub outline_until: Option<Instant>,
    pub lines: Vec<String>,
}

impl Display {
    pub fn new(screen: Option<Screen>) -> Self {
        let mut display = Display { screen, look: Look::default(), outline_until: None, lines: Vec::new() };
        display.apply_to_screen();

        display
    }

    pub fn apply_to_screen(&mut self) {
        let Some(screen) = self.screen.as_mut() else { return };
        let rotation = if self.look.flip { DisplayRotation::Rotate180 } else { DisplayRotation::Rotate0 };
        let contrast = (self.look.brightness * 255.0).round().clamp(0.0, 255.0) as u8;

        let _ = screen.set_rotation(rotation);
        let _ = screen.set_mirror(self.look.mirror != self.look.flip);
        let _ = screen.set_brightness(Brightness::custom(2, contrast));
    }

    pub fn set_look(&mut self, look: Look) {
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

    pub fn show(&mut self, lines: &[&str]) {
        println!("{}", lines.join(" | "));
        self.lines = lines.iter().map(|line| line.to_string()).collect();
        self.draw();
    }

    pub fn area(&self) -> (Point, i32, i32) {
        let (width, height) = ((128.0 * self.look.scale).round() as i32, (64.0 * self.look.scale).round() as i32);
        let x = (128 - width) / 2 + ((128 - width) as f64 / 2.0 * self.look.x).round() as i32;
        let y = (64 - height) / 2 + ((64 - height) as f64 / 2.0 * self.look.y).round() as i32;

        (Point::new(x, y), width, height)
    }

    pub fn draw(&mut self) {
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
