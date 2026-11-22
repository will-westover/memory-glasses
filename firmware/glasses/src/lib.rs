pub mod display;
pub mod link;
pub mod online;
pub mod state;

use esp_idf_svc::sys::camera;

pub const WARM_UP_FRAMES: usize = 3;

pub fn xiao_sense_camera_config(frame_size: camera::framesize_t) -> camera::camera_config_t {
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
        frame_size,
        jpeg_quality: 12,
        fb_count: 1,
        fb_location: camera::camera_fb_location_t_CAMERA_FB_IN_PSRAM,
        grab_mode: camera::camera_grab_mode_t_CAMERA_GRAB_LATEST,
        ..Default::default()
    }
}

pub fn take_photo() -> Option<(Vec<u8>, usize, usize)> {
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

pub const PHOTO_FRAME_SIZE: camera::framesize_t = camera::framesize_t_FRAMESIZE_UXGA;
pub const STREAM_FRAME_SIZE: camera::framesize_t = camera::framesize_t_FRAMESIZE_XGA;

pub fn start_camera(frame_size: camera::framesize_t) -> Result<(), i32> {
    let error_code = unsafe { camera::esp_camera_init(&xiao_sense_camera_config(frame_size)) };

    if error_code != 0 {
        return Err(error_code);
    }

    for _ in 0..WARM_UP_FRAMES {
        take_photo();
        std::thread::sleep(std::time::Duration::from_millis(200));
    }

    Ok(())
}
