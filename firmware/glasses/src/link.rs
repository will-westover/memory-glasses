use std::sync::atomic::{AtomicBool, AtomicU16, AtomicU8, Ordering};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use esp32_nimble::utilities::mutex::Mutex;
use esp32_nimble::utilities::BleUuid;
use esp32_nimble::{uuid128, BLEAdvertisementData, BLECharacteristic, BLEDevice, NimbleProperties};
use esp_idf_svc::sys;

const SERVICE: BleUuid = uuid128!("6e1f0001-6d65-6d6f-7279-676c61737365");
const FRAME: BleUuid = uuid128!("6e1f0002-6d65-6d6f-7279-676c61737365");
const SETTINGS: BleUuid = uuid128!("6e1f0003-6d65-6d6f-7279-676c61737365");
const ACK: BleUuid = uuid128!("6e1f0004-6d65-6d6f-7279-676c61737365");
const UPDATE: BleUuid = uuid128!("6e1f0005-6d65-6d6f-7279-676c61737365");
const UPDATE_STATUS: BleUuid = uuid128!("6e1f0006-6d65-6d6f-7279-676c61737365");
const VERSION: BleUuid = uuid128!("6e1f0007-6d65-6d6f-7279-676c61737365");

const NAME: &str = "Memory Glasses";
const UPDATE_STEP: usize = 32 * 1024;

pub enum Message {
    Settings(Vec<u8>),
    Ack(u32),
}

pub struct Link {
    frame: Arc<Mutex<BLECharacteristic>>,
    messages: Receiver<Message>,
    connected: Arc<AtomicBool>,
    subscribed: Arc<AtomicBool>,
    mtu: Arc<AtomicU16>,
    progress: Arc<AtomicU8>,
    updating: Arc<AtomicBool>,
}

fn faster_link(handle: u16) {
    unsafe {
        esp_idf_svc::sys::ble_gap_set_prefered_le_phy(handle, 0x02, 0x02, 0);
        esp_idf_svc::sys::ble_gap_set_data_len(handle, 251, 2120);
    }
}


pub fn crc32(data: &[u8]) -> u32 {
    crc32_continue(0, data)
}

fn crc32_continue(crc: u32, data: &[u8]) -> u32 {
    let mut crc = !crc;

    for &byte in data {
        crc ^= byte as u32;

        for _ in 0..8 {
            crc = if crc & 1 != 0 { (crc >> 1) ^ 0xEDB8_8320 } else { crc >> 1 };
        }
    }

    !crc
}

fn word(bytes: &[u8]) -> Option<u32> {
    Some(u32::from_le_bytes(bytes.get(..4)?.try_into().ok()?))
}

impl Link {
    pub fn start(version: &str) -> Link {
        let device = BLEDevice::take();
        let _ = BLEDevice::set_device_name(NAME);
        let _ = device.set_preferred_mtu(517);

        let server = device.get_server();
        let advertising = device.get_advertising();
        let connected = Arc::new(AtomicBool::new(false));
        let subscribed = Arc::new(AtomicBool::new(false));
        let mtu = Arc::new(AtomicU16::new(23));
        let progress = Arc::new(AtomicU8::new(0));
        let updating = Arc::new(AtomicBool::new(false));
        let (send, messages) = channel();
        let (update_send, update_receive) = channel::<Vec<u8>>();

        server.advertise_on_disconnect(true);

        let on_connect = (connected.clone(), mtu.clone());
        server.on_connect(move |server, desc| {
            on_connect.0.store(true, Ordering::SeqCst);
            on_connect.1.store(desc.mtu(), Ordering::SeqCst);
            let _ = server.update_conn_params(desc.conn_handle(), 6, 12, 0, 400);
            faster_link(desc.conn_handle());
            println!("phone connected, mtu {}", desc.mtu());
        });

        let on_disconnect = (connected.clone(), subscribed.clone());
        server.on_disconnect(move |_, reason| {
            on_disconnect.0.store(false, Ordering::SeqCst);
            on_disconnect.1.store(false, Ordering::SeqCst);
            println!("phone disconnected: {reason:?}");
        });

        let service = server.create_service(SERVICE);

        let frame = service.lock().create_characteristic(FRAME, NimbleProperties::NOTIFY);
        let on_subscribe = (subscribed.clone(), mtu.clone());
        frame.lock().on_subscribe(move |characteristic, desc, _| {
            on_subscribe.0.store(characteristic.subscribed_count() > 0, Ordering::SeqCst);
            on_subscribe.1.store(desc.mtu(), Ordering::SeqCst);
        });

        let settings_send = send.clone();
        service.lock().create_characteristic(SETTINGS, NimbleProperties::WRITE).lock().on_write(move |args| {
            let _ = settings_send.send(Message::Settings(args.recv_data().to_vec()));
        });

        service.lock().create_characteristic(ACK, NimbleProperties::WRITE_NO_RSP).lock().on_write(move |args| {
            if let Some(number) = word(args.recv_data()) {
                let _ = send.send(Message::Ack(number));
            }
        });

        service.lock().create_characteristic(UPDATE, NimbleProperties::WRITE_NO_RSP).lock().on_write(move |args| {
            let _ = update_send.send(args.recv_data().to_vec());
        });

        let update_status = service.lock().create_characteristic(UPDATE_STATUS, NimbleProperties::NOTIFY);

        service.lock().create_characteristic(VERSION, NimbleProperties::READ).lock().set_value(version.as_bytes());

        let mut data = BLEAdvertisementData::new();
        data.name(NAME).add_service_uuid(SERVICE);
        let _ = advertising.lock().scan_response(true).set_data(&mut data);
        let _ = advertising.lock().start();

        let worker = (progress.clone(), updating.clone());
        thread::Builder::new()
            .stack_size(8192)
            .spawn(move || run_updates(update_receive, update_status, worker.0, worker.1))
            .expect("update thread");

        Link { frame, messages, connected, subscribed, mtu, progress, updating }
    }

    pub fn ready(&self) -> bool {
        self.connected.load(Ordering::SeqCst) && self.subscribed.load(Ordering::SeqCst)
    }

    pub fn updating(&self) -> Option<u8> {
        self.updating.load(Ordering::SeqCst).then(|| self.progress.load(Ordering::SeqCst))
    }

    pub fn next_message(&self, wait: Duration) -> Option<Message> {
        match self.messages.recv_timeout(wait) {
            Ok(message) => Some(message),
            Err(RecvTimeoutError::Timeout) | Err(RecvTimeoutError::Disconnected) => None,
        }
    }

    fn notify(&self, value: &[u8]) -> bool {
        let deadline = Instant::now() + Duration::from_secs(5);

        loop {
            if !self.ready() {
                return false;
            }

            let sent = {
                let characteristic = self.frame.lock();
                let handle = BLEDevice::take().get_server().connections().next().map(|desc| desc.conn_handle());
                handle.map(|handle| characteristic.notify_with(value, handle).is_ok()).unwrap_or(false)
            };

            if sent {
                return true;
            }

            if Instant::now() > deadline {
                return false;
            }

            thread::sleep(Duration::from_millis(3));
        }
    }

    pub fn send_frame(&self, jpeg: &[u8], number: u32, recording: &str) -> bool {
        let mut start = vec![1u8];
        start.extend_from_slice(&(jpeg.len() as u32).to_le_bytes());
        start.extend_from_slice(&number.to_le_bytes());
        start.extend_from_slice(&recording.as_bytes()[..recording.len().min(16)]);

        if !self.notify(&start) {
            return false;
        }

        let mtu = BLEDevice::take().get_server().connections().next().map(|desc| desc.mtu())
            .unwrap_or_else(|| self.mtu.load(Ordering::SeqCst));
        self.mtu.store(mtu, Ordering::SeqCst);
        let chunk = (mtu as usize).saturating_sub(4).max(19);

        for piece in jpeg.chunks(chunk) {
            let mut data = Vec::with_capacity(piece.len() + 1);
            data.push(2u8);
            data.extend_from_slice(piece);

            if !self.notify(&data) {
                return false;
            }
        }

        let mut end = vec![3u8];
        end.extend_from_slice(&crc32(jpeg).to_le_bytes());

        self.notify(&end)
    }
}

struct Update {
    handle: sys::esp_ota_handle_t,
    partition: *const sys::esp_partition_t,
    size: u32,
    crc: u32,
    received: u32,
    running_crc: u32,
    reported: u32,
}

unsafe impl Send for Update {}

fn say(status: &Arc<Mutex<BLECharacteristic>>, text: &str) {
    println!("update: {text}");
    status.lock().set_value(text.as_bytes()).notify();
}

fn abandon(update: &mut Option<Update>) {
    if let Some(current) = update.take() {
        unsafe { sys::esp_ota_abort(current.handle) };
    }
}

fn begin(payload: &[u8]) -> Result<Update, String> {
    let size = word(payload).ok_or("short begin")?;
    let crc = word(payload.get(4..).unwrap_or_default()).ok_or("short begin")?;

    let partition = unsafe { sys::esp_ota_get_next_update_partition(std::ptr::null()) };
    if partition.is_null() {
        return Err("no update slot".into());
    }

    if size > unsafe { (*partition).size } {
        return Err("image too big".into());
    }

    let mut handle: sys::esp_ota_handle_t = 0;
    let result = unsafe { sys::esp_ota_begin(partition, size as usize, &mut handle) };
    if result != sys::ESP_OK {
        return Err(format!("begin failed {result}"));
    }

    Ok(Update { handle, partition, size, crc, received: 0, running_crc: 0, reported: 0 })
}

fn finish(update: Update) -> Result<(), String> {
    if update.received != update.size {
        unsafe { sys::esp_ota_abort(update.handle) };
        return Err(format!("got {} of {} bytes", update.received, update.size));
    }

    if update.running_crc != update.crc {
        unsafe { sys::esp_ota_abort(update.handle) };
        return Err("crc mismatch".into());
    }

    let result = unsafe { sys::esp_ota_end(update.handle) };
    if result != sys::ESP_OK {
        return Err(format!("image rejected {result}"));
    }

    let result = unsafe { sys::esp_ota_set_boot_partition(update.partition) };
    if result != sys::ESP_OK {
        return Err(format!("set boot failed {result}"));
    }

    Ok(())
}

fn run_updates(incoming: Receiver<Vec<u8>>, status: Arc<Mutex<BLECharacteristic>>, progress: Arc<AtomicU8>,
               updating: Arc<AtomicBool>) {
    let mut update: Option<Update> = None;

    for message in incoming {
        let Some((&kind, payload)) = message.split_first() else { continue };

        match kind {
            1 => {
                abandon(&mut update);
                updating.store(true, Ordering::SeqCst);
                progress.store(0, Ordering::SeqCst);

                match begin(payload) {
                    Ok(started) => {
                        update = Some(started);
                        say(&status, "ready");
                    }
                    Err(reason) => {
                        updating.store(false, Ordering::SeqCst);
                        say(&status, &format!("error {reason}"));
                    }
                }
            }
            2 => {
                let Some(current) = update.as_mut() else { continue };

                if current.received + payload.len() as u32 > current.size {
                    abandon(&mut update);
                    updating.store(false, Ordering::SeqCst);
                    say(&status, "error too many bytes");
                    continue;
                }

                let result = unsafe { sys::esp_ota_write(current.handle, payload.as_ptr() as _, payload.len()) };
                if result != sys::ESP_OK {
                    abandon(&mut update);
                    updating.store(false, Ordering::SeqCst);
                    say(&status, &format!("error write failed {result}"));
                    continue;
                }

                current.received += payload.len() as u32;
                current.running_crc = crc32_continue(current.running_crc, payload);
                progress.store((current.received as u64 * 100 / current.size.max(1) as u64) as u8, Ordering::SeqCst);

                if current.received / UPDATE_STEP as u32 > current.reported {
                    current.reported = current.received / UPDATE_STEP as u32;
                    let text = format!("got {}", current.received);
                    say(&status, &text);
                }
            }
            3 => {
                let Some(current) = update.take() else { continue };

                match finish(current) {
                    Ok(()) => {
                        say(&status, "done");
                        thread::sleep(Duration::from_millis(500));
                        unsafe { sys::esp_restart() };
                    }
                    Err(reason) => {
                        updating.store(false, Ordering::SeqCst);
                        say(&status, &format!("error {reason}"));
                    }
                }
            }
            4 => {
                abandon(&mut update);
                updating.store(false, Ordering::SeqCst);
                say(&status, "error cancelled");
            }
            _ => {}
        }
    }
}

pub fn keep_this_firmware() {
    unsafe { sys::esp_ota_mark_app_valid_cancel_rollback() };
}
