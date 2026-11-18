use std::thread;
use std::time::Duration;

use base64::Engine;
use glasses::start_camera;
use glasses::take_photo;

const LINE_LENGTH: usize = 76;

fn print_photo(bytes: &[u8], width: usize, height: usize) {
    let encoded = base64::engine::general_purpose::STANDARD.encode(bytes);

    println!("PHOTO {} bytes {}x{}", bytes.len(), width, height);

    for line in encoded.as_bytes().chunks(LINE_LENGTH) {
        println!("{}", std::str::from_utf8(line).unwrap());
    }

    println!("END PHOTO");
}

fn main() {
    esp_idf_svc::sys::link_patches();
    esp_idf_svc::log::EspLogger::initialize_default();

    if let Err(error) = start_camera(glasses::PHOTO) {
        log::error!("camera failed to start: error {error}");
        return;
    }

    match take_photo() {
        Some((bytes, width, height)) => print_photo(&bytes, width, height),
        None => log::error!("camera returned no frame"),
    }

    loop {
        thread::sleep(Duration::from_secs(60));
    }
}
