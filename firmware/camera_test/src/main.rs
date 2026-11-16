use std::thread;
use std::time::Duration;

use base64::Engine;
use esp_idf_svc::sys::camera;

const WARM_UP_FRAMES: usize = 3;
const LINE_LENGTH: usize = 76;

fn xiao_sense_camera() -> camera::camera_config_t {
    camera::camera_config_t {
        pin_pwdn: -1,
        pin_reset: -1,
        pin_xclk: 10,
        __bindgen_anon_1: camera::camera_config_t__bindgen_ty_1 { pin_sccb_sda: 40 },
        __bindgen_anon_2: camera::camera_config_t__bindgen_ty_2 { pin_sccb_scl: 39 },
        pin_d7: 48,
        pin_d6: 11,
        pin_d5: 12,
        pin_d4: 14,
        pin_d3: 16,
        pin_d2: 18,
        pin_d1: 17,
        pin_d0: 15,
        pin_vsync: 38,
        pin_href: 47,
        pin_pclk: 13,
        xclk_freq_hz: 20_000_000,
        pixel_format: camera::pixformat_t_PIXFORMAT_JPEG,
        frame_size: camera::framesize_t_FRAMESIZE_UXGA,
        jpeg_quality: 12,
        fb_count: 1,
        fb_location: camera::camera_fb_location_t_CAMERA_FB_IN_PSRAM,
        grab_mode: camera::camera_grab_mode_t_CAMERA_GRAB_LATEST,
        ..Default::default()
    }
}

fn take_photo() -> Option<(Vec<u8>, usize, usize)> {
    let frame = unsafe { camera::esp_camera_fb_get() };

    if frame.is_null() {
        return None;
    }

    let photo = unsafe {
        let frame = &*frame;
        let bytes = std::slice::from_raw_parts(frame.buf, frame.len).to_vec();

        (bytes, frame.width, frame.height)
    };

    unsafe { camera::esp_camera_fb_return(frame) };

    Some(photo)
}

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

    let started = unsafe { camera::esp_camera_init(&xiao_sense_camera()) };

    if started != 0 {
        log::error!("camera failed to start: error {started}");
        return;
    }

    for _ in 0..WARM_UP_FRAMES {
        take_photo();
        thread::sleep(Duration::from_millis(200));
    }

    match take_photo() {
        Some((bytes, width, height)) => print_photo(&bytes, width, height),
        None => log::error!("camera returned no frame"),
    }

    loop {
        thread::sleep(Duration::from_secs(60));
    }
}
