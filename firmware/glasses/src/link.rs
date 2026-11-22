use std::sync::atomic::{AtomicBool, AtomicU16, AtomicU8, Ordering};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use esp32_nimble::utilities::mutex::Mutex;
use esp32_nimble::utilities::BleUuid;
use esp32_nimble::{BLEAdvertisementData, BLECharacteristic, BLEDevice, NimbleProperties};
use esp_idf_svc::sys;
use protocol::{auth, frame, read_u32_le, update};

fn uuid(text: &str) -> BleUuid {
    BleUuid::from_uuid128_string(text).expect("a valid UUID")
}

pub enum PhoneMessage {
    Settings(Vec<u8>),
    Ack(u32),
}

pub struct Link {
    frame_characteristic: Arc<Mutex<BLECharacteristic>>,
    messages: Receiver<PhoneMessage>,
    connected: Arc<AtomicBool>,
    subscribed: Arc<AtomicBool>,
    trusted: Arc<AtomicBool>,
    mtu: Arc<AtomicU16>,
    update_percent: Arc<AtomicU8>,
    update_in_progress: Arc<AtomicBool>,
}

struct Nonces {
    phone: [u8; auth::NONCE_BYTES],
    glasses: [u8; auth::NONCE_BYTES],
}

fn random_nonce() -> [u8; auth::NONCE_BYTES] {
    let mut nonce = [0u8; auth::NONCE_BYTES];

    for part in nonce.chunks_mut(4) {
        part.copy_from_slice(&unsafe { sys::esp_random() }.to_le_bytes());
    }

    nonce
}

fn fresh_nonces() -> Nonces {
    Nonces { phone: random_nonce(), glasses: random_nonce() }
}

impl Link {
    pub fn start(version: &str, token: &'static str) -> Link {
        let device = BLEDevice::take();
        let _ = BLEDevice::set_device_name(protocol::DEVICE_NAME);
        let _ = device.set_preferred_mtu(517);

        let server = device.get_server();
        let advertising = device.get_advertising();
        let connected = Arc::new(AtomicBool::new(false));
        let subscribed = Arc::new(AtomicBool::new(false));
        let trusted = Arc::new(AtomicBool::new(token.is_empty()));
        let nonces = Arc::new(Mutex::new(fresh_nonces()));
        let mtu = Arc::new(AtomicU16::new(23));
        let update_percent = Arc::new(AtomicU8::new(0));
        let update_in_progress = Arc::new(AtomicBool::new(false));
        let (message_sender, messages) = channel();
        let (update_sender, update_receiver) = channel::<Vec<u8>>();

        server.advertise_on_disconnect(true);

        let (connect_connected, connect_mtu, connect_trusted, connect_nonces) =
            (connected.clone(), mtu.clone(), trusted.clone(), nonces.clone());
        server.on_connect(move |server, desc| {
            connect_connected.store(true, Ordering::SeqCst);
            connect_mtu.store(desc.mtu(), Ordering::SeqCst);
            connect_trusted.store(token.is_empty(), Ordering::SeqCst);
            *connect_nonces.lock() = fresh_nonces();
            let _ = server.update_conn_params(desc.conn_handle(), 6, 12, 0, 400);
            println!("phone connected, mtu {}", desc.mtu());
        });

        let (gone_connected, gone_subscribed, gone_trusted, gone_update_in_progress, gone_update_sender) =
            (connected.clone(), subscribed.clone(), trusted.clone(), update_in_progress.clone(), update_sender.clone());
        server.on_disconnect(move |_, reason| {
            gone_connected.store(false, Ordering::SeqCst);
            gone_subscribed.store(false, Ordering::SeqCst);
            gone_trusted.store(token.is_empty(), Ordering::SeqCst);

            if gone_update_in_progress.load(Ordering::SeqCst) {
                let _ = gone_update_sender.send(vec![update::CANCEL]);
            }

            println!("phone disconnected: {reason:?}");
        });

        let service = server.create_service(uuid(protocol::SERVICE_UUID));

        let frame_characteristic = service.lock().create_characteristic(uuid(protocol::FRAME_UUID), NimbleProperties::NOTIFY);
        let (subscribe_subscribed, subscribe_mtu) = (subscribed.clone(), mtu.clone());
        frame_characteristic.lock().on_subscribe(move |characteristic, desc, _| {
            subscribe_subscribed.store(characteristic.subscribed_count() > 0, Ordering::SeqCst);
            subscribe_mtu.store(desc.mtu(), Ordering::SeqCst);
        });

        let (settings_sender, settings_trusted) = (message_sender.clone(), trusted.clone());
        service.lock().create_characteristic(uuid(protocol::SETTINGS_UUID), NimbleProperties::WRITE).lock().on_write(move |args| {
            if settings_trusted.load(Ordering::SeqCst) {
                let _ = settings_sender.send(PhoneMessage::Settings(args.recv_data().to_vec()));
            }
        });

        let ack_trusted = trusted.clone();
        service.lock().create_characteristic(uuid(protocol::ACK_UUID), NimbleProperties::WRITE_NO_RSP).lock().on_write(move |args| {
            if let Some(number) = read_u32_le(args.recv_data()).filter(|_| ack_trusted.load(Ordering::SeqCst)) {
                let _ = message_sender.send(PhoneMessage::Ack(number));
            }
        });

        let update_trusted = trusted.clone();
        service.lock().create_characteristic(uuid(protocol::UPDATE_UUID), NimbleProperties::WRITE_NO_RSP).lock().on_write(move |args| {
            if update_trusted.load(Ordering::SeqCst) {
                let _ = update_sender.send(args.recv_data().to_vec());
            }
        });

        if !token.is_empty() {
            let auth_characteristic = service.lock().create_characteristic(uuid(protocol::AUTH_UUID), NimbleProperties::READ | NimbleProperties::WRITE);
            let read_nonces = nonces.clone();
            let (write_nonces, write_trusted) = (nonces.clone(), trusted.clone());

            auth_characteristic.lock().on_read(move |characteristic, _| {
                let nonces = read_nonces.lock();
                characteristic.set_value(&auth::glasses_challenge_answer(token, &nonces.phone, &nonces.glasses));
            });

            auth_characteristic.lock().on_write(move |args| {
                let written = args.recv_data();
                let mut nonces = write_nonces.lock();

                if written.len() == auth::NONCE_BYTES {
                    nonces.phone.copy_from_slice(written);
                    nonces.glasses = random_nonce();
                    write_trusted.store(false, Ordering::SeqCst);
                } else if written.len() == auth::PROOF_BYTES {
                    let proved = auth::verify_phone_proof(token, &nonces.phone, &nonces.glasses, written);
                    write_trusted.store(proved, Ordering::SeqCst);
                    nonces.glasses = random_nonce();
                }
            });
        }

        let status_characteristic = service.lock().create_characteristic(uuid(protocol::UPDATE_STATUS_UUID), NimbleProperties::NOTIFY);

        service.lock().create_characteristic(uuid(protocol::VERSION_UUID), NimbleProperties::READ).lock().set_value(version.as_bytes());

        let mut advertisement = BLEAdvertisementData::new();
        advertisement.name(protocol::DEVICE_NAME).add_service_uuid(uuid(protocol::SERVICE_UUID));
        let _ = advertising.lock().scan_response(true).set_data(&mut advertisement);
        let _ = advertising.lock().start();

        let (thread_percent, thread_in_progress) = (update_percent.clone(), update_in_progress.clone());
        thread::Builder::new()
            .stack_size(8192)
            .spawn(move || run_updates(update_receiver, status_characteristic, thread_percent, thread_in_progress))
            .expect("update thread");

        Link { frame_characteristic, messages, connected, subscribed, trusted, mtu, update_percent, update_in_progress }
    }

    pub fn phone_ready(&self) -> bool {
        self.connected.load(Ordering::SeqCst) && self.subscribed.load(Ordering::SeqCst)
            && self.trusted.load(Ordering::SeqCst)
    }

    pub fn update_progress(&self) -> Option<u8> {
        self.update_in_progress.load(Ordering::SeqCst).then(|| self.update_percent.load(Ordering::SeqCst))
    }

    pub fn next_message(&self, wait: Duration) -> Option<PhoneMessage> {
        match self.messages.recv_timeout(wait) {
            Ok(message) => Some(message),
            Err(RecvTimeoutError::Timeout) | Err(RecvTimeoutError::Disconnected) => None,
        }
    }

    fn notify_frame_packet(&self, packet: &[u8]) -> bool {
        let deadline = Instant::now() + Duration::from_secs(5);

        loop {
            if !self.phone_ready() {
                return false;
            }

            let sent = {
                let characteristic = self.frame_characteristic.lock();
                let handle = BLEDevice::take().get_server().connections().next().map(|desc| desc.conn_handle());
                handle.map(|handle| characteristic.notify_with(packet, handle).is_ok()).unwrap_or(false)
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

    pub fn send_frame(&self, jpeg: &[u8], number: u32, recording_id: &str) -> bool {
        if !self.notify_frame_packet(&frame::start(jpeg.len() as u32, number, recording_id)) {
            return false;
        }

        let mtu = BLEDevice::take().get_server().connections().next().map(|desc| desc.mtu())
            .unwrap_or_else(|| self.mtu.load(Ordering::SeqCst));

        for piece in jpeg.chunks(frame::piece_size(mtu)) {
            if !self.notify_frame_packet(&frame::data(piece)) {
                return false;
            }
        }

        self.notify_frame_packet(&frame::end(protocol::crc32(jpeg)))
    }
}

struct FirmwareUpdate {
    ota_handle: sys::esp_ota_handle_t,
    partition: *const sys::esp_partition_t,
    expected_bytes: u32,
    expected_crc: u32,
    received_bytes: u32,
    received_crc: u32,
    steps_reported: u32,
}

unsafe impl Send for FirmwareUpdate {}

fn report_update_status(status_characteristic: &Arc<Mutex<BLECharacteristic>>, status: update::Status) {
    let text = status.text();

    println!("update: {text}");
    status_characteristic.lock().set_value(text.as_bytes()).notify();
}

fn report_update_error(status_characteristic: &Arc<Mutex<BLECharacteristic>>, reason: impl Into<String>) {
    report_update_status(status_characteristic, update::Status::Error(reason.into()));
}

fn abort_update(update: &mut Option<FirmwareUpdate>) {
    if let Some(in_progress) = update.take() {
        unsafe { sys::esp_ota_abort(in_progress.ota_handle) };
    }
}

fn begin_firmware_write(expected_bytes: u32, expected_crc: u32) -> Result<FirmwareUpdate, String> {
    let partition = unsafe { sys::esp_ota_get_next_update_partition(std::ptr::null()) };
    if partition.is_null() {
        return Err("no update slot".into());
    }

    if expected_bytes > unsafe { (*partition).size } {
        return Err("image too big".into());
    }

    let mut ota_handle: sys::esp_ota_handle_t = 0;
    let result = unsafe { sys::esp_ota_begin(partition, expected_bytes as usize, &mut ota_handle) };
    if result != sys::ESP_OK {
        return Err(format!("begin failed {result}"));
    }

    Ok(FirmwareUpdate { ota_handle, partition, expected_bytes, expected_crc, received_bytes: 0, received_crc: 0,
                        steps_reported: 0 })
}

fn install_update(update: FirmwareUpdate) -> Result<(), String> {
    if update.received_bytes != update.expected_bytes {
        unsafe { sys::esp_ota_abort(update.ota_handle) };
        return Err(format!("got {} of {} bytes", update.received_bytes, update.expected_bytes));
    }

    if update.received_crc != update.expected_crc {
        unsafe { sys::esp_ota_abort(update.ota_handle) };
        return Err("crc mismatch".into());
    }

    let result = unsafe { sys::esp_ota_end(update.ota_handle) };
    if result != sys::ESP_OK {
        return Err(format!("image rejected {result}"));
    }

    let result = unsafe { sys::esp_ota_set_boot_partition(update.partition) };
    if result != sys::ESP_OK {
        return Err(format!("set boot failed {result}"));
    }

    Ok(())
}

fn run_updates(update_packets: Receiver<Vec<u8>>, status_characteristic: Arc<Mutex<BLECharacteristic>>,
               update_percent: Arc<AtomicU8>, update_in_progress: Arc<AtomicBool>) {
    let mut update: Option<FirmwareUpdate> = None;

    for packet in update_packets {
        match update::parse(&packet) {
            Some(update::Packet::Begin { size, crc }) => {
                abort_update(&mut update);
                update_in_progress.store(true, Ordering::SeqCst);
                update_percent.store(0, Ordering::SeqCst);

                match begin_firmware_write(size, crc) {
                    Ok(started) => {
                        update = Some(started);
                        report_update_status(&status_characteristic, update::Status::Ready);
                    }
                    Err(reason) => {
                        update_in_progress.store(false, Ordering::SeqCst);
                        report_update_error(&status_characteristic, reason);
                    }
                }
            }
            Some(update::Packet::Data(piece)) => {
                let Some(in_progress) = update.as_mut() else { continue };

                if in_progress.received_bytes + piece.len() as u32 > in_progress.expected_bytes {
                    abort_update(&mut update);
                    update_in_progress.store(false, Ordering::SeqCst);
                    report_update_error(&status_characteristic, "too many bytes");
                    continue;
                }

                let result = unsafe { sys::esp_ota_write(in_progress.ota_handle, piece.as_ptr() as _, piece.len()) };
                if result != sys::ESP_OK {
                    abort_update(&mut update);
                    update_in_progress.store(false, Ordering::SeqCst);
                    report_update_error(&status_characteristic, format!("write failed {result}"));
                    continue;
                }

                in_progress.received_bytes += piece.len() as u32;
                in_progress.received_crc = protocol::crc32_continue(in_progress.received_crc, piece);

                let percent = in_progress.received_bytes as u64 * 100 / in_progress.expected_bytes.max(1) as u64;
                update_percent.store(percent as u8, Ordering::SeqCst);

                let steps = in_progress.received_bytes / update::STATUS_EVERY_BYTES as u32;
                if steps > in_progress.steps_reported {
                    in_progress.steps_reported = steps;
                    report_update_status(&status_characteristic, update::Status::Received(in_progress.received_bytes));
                }
            }
            Some(update::Packet::End) => {
                let Some(received) = update.take() else { continue };

                match install_update(received) {
                    Ok(()) => {
                        report_update_status(&status_characteristic, update::Status::Done);
                        thread::sleep(Duration::from_millis(500));
                        unsafe { sys::esp_restart() };
                    }
                    Err(reason) => {
                        update_in_progress.store(false, Ordering::SeqCst);
                        report_update_error(&status_characteristic, reason);
                    }
                }
            }
            Some(update::Packet::Cancel) => {
                abort_update(&mut update);
                update_in_progress.store(false, Ordering::SeqCst);
                report_update_error(&status_characteristic, "cancelled");
            }
            None => {}
        }
    }
}

pub fn mark_firmware_valid() {
    unsafe { sys::esp_ota_mark_app_valid_cancel_rollback() };
}
