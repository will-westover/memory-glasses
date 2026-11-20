use std::sync::atomic::{AtomicBool, AtomicU16, AtomicU8, Ordering};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use esp32_nimble::utilities::mutex::Mutex;
use esp32_nimble::utilities::BleUuid;
use esp32_nimble::{BLEAdvertisementData, BLECharacteristic, BLEDevice, NimbleProperties};
use esp_idf_svc::sys;
use protocol::{auth, frame, update, word};

fn uuid(text: &str) -> BleUuid {
    BleUuid::from_uuid128_string(text).expect("a valid UUID")
}


pub enum Message {
    Settings(Vec<u8>),
    Ack(u32),
}

pub struct Link {
    frame: Arc<Mutex<BLECharacteristic>>,
    messages: Receiver<Message>,
    connected: Arc<AtomicBool>,
    subscribed: Arc<AtomicBool>,
    trusted: Arc<AtomicBool>,
    mtu: Arc<AtomicU16>,
    progress: Arc<AtomicU8>,
    updating: Arc<AtomicBool>,
}

fn random_nonce() -> [u8; auth::NONCE_BYTES] {
    let mut nonce = [0u8; auth::NONCE_BYTES];

    for part in nonce.chunks_mut(4) {
        part.copy_from_slice(&unsafe { sys::esp_random() }.to_le_bytes());
    }

    nonce
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
        let nonces = Arc::new(Mutex::new((random_nonce(), random_nonce())));
        let mtu = Arc::new(AtomicU16::new(23));
        let progress = Arc::new(AtomicU8::new(0));
        let updating = Arc::new(AtomicBool::new(false));
        let (send, messages) = channel();
        let (update_send, update_receive) = channel::<Vec<u8>>();

        server.advertise_on_disconnect(true);

        let on_connect = (connected.clone(), mtu.clone(), trusted.clone(), nonces.clone());
        server.on_connect(move |server, desc| {
            on_connect.0.store(true, Ordering::SeqCst);
            on_connect.1.store(desc.mtu(), Ordering::SeqCst);
            on_connect.2.store(token.is_empty(), Ordering::SeqCst);
            *on_connect.3.lock() = (random_nonce(), random_nonce());
            let _ = server.update_conn_params(desc.conn_handle(), 6, 12, 0, 400);
            println!("phone connected, mtu {}", desc.mtu());
        });

        let on_disconnect = (connected.clone(), subscribed.clone(), trusted.clone(), updating.clone(), update_send.clone());
        server.on_disconnect(move |_, reason| {
            on_disconnect.0.store(false, Ordering::SeqCst);
            on_disconnect.1.store(false, Ordering::SeqCst);
            on_disconnect.2.store(token.is_empty(), Ordering::SeqCst);

            if on_disconnect.3.load(Ordering::SeqCst) {
                let _ = on_disconnect.4.send(vec![update::CANCEL]);
            }

            println!("phone disconnected: {reason:?}");
        });

        let service = server.create_service(uuid(protocol::SERVICE));

        let frame = service.lock().create_characteristic(uuid(protocol::FRAME), NimbleProperties::NOTIFY);
        let on_subscribe = (subscribed.clone(), mtu.clone());
        frame.lock().on_subscribe(move |characteristic, desc, _| {
            on_subscribe.0.store(characteristic.subscribed_count() > 0, Ordering::SeqCst);
            on_subscribe.1.store(desc.mtu(), Ordering::SeqCst);
        });

        let settings = (send.clone(), trusted.clone());
        service.lock().create_characteristic(uuid(protocol::SETTINGS), NimbleProperties::WRITE).lock().on_write(move |args| {
            if settings.1.load(Ordering::SeqCst) {
                let _ = settings.0.send(Message::Settings(args.recv_data().to_vec()));
            }
        });

        let ack_trusted = trusted.clone();
        service.lock().create_characteristic(uuid(protocol::ACK), NimbleProperties::WRITE_NO_RSP).lock().on_write(move |args| {
            if let Some(number) = word(args.recv_data()).filter(|_| ack_trusted.load(Ordering::SeqCst)) {
                let _ = send.send(Message::Ack(number));
            }
        });

        let update_trusted = trusted.clone();
        service.lock().create_characteristic(uuid(protocol::UPDATE), NimbleProperties::WRITE_NO_RSP).lock().on_write(move |args| {
            if update_trusted.load(Ordering::SeqCst) {
                let _ = update_send.send(args.recv_data().to_vec());
            }
        });

        if !token.is_empty() {
            let auth = service.lock().create_characteristic(uuid(protocol::AUTH), NimbleProperties::READ | NimbleProperties::WRITE);
            let asked = nonces.clone();
            let answered = (nonces.clone(), trusted.clone());

            auth.lock().on_read(move |characteristic, _| {
                let pair = asked.lock();
                characteristic.set_value(&auth::glasses_answer(token, &pair.0, &pair.1));
            });

            auth.lock().on_write(move |args| {
                let data = args.recv_data();
                let mut pair = answered.0.lock();

                if data.len() == auth::NONCE_BYTES {
                    pair.0.copy_from_slice(data);
                    pair.1 = random_nonce();
                    answered.1.store(false, Ordering::SeqCst);
                } else if data.len() == auth::PROOF_BYTES {
                    answered.1.store(auth::phone_is_right(token, &pair.0, &pair.1, data), Ordering::SeqCst);
                    pair.1 = random_nonce();
                }
            });
        }

        let update_status = service.lock().create_characteristic(uuid(protocol::UPDATE_STATUS), NimbleProperties::NOTIFY);

        service.lock().create_characteristic(uuid(protocol::VERSION), NimbleProperties::READ).lock().set_value(version.as_bytes());

        let mut data = BLEAdvertisementData::new();
        data.name(protocol::DEVICE_NAME).add_service_uuid(uuid(protocol::SERVICE));
        let _ = advertising.lock().scan_response(true).set_data(&mut data);
        let _ = advertising.lock().start();

        let worker = (progress.clone(), updating.clone());
        thread::Builder::new()
            .stack_size(8192)
            .spawn(move || run_updates(update_receive, update_status, worker.0, worker.1))
            .expect("update thread");

        Link { frame, messages, connected, subscribed, trusted, mtu, progress, updating }
    }

    pub fn ready(&self) -> bool {
        self.connected.load(Ordering::SeqCst) && self.subscribed.load(Ordering::SeqCst)
            && self.trusted.load(Ordering::SeqCst)
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
        if !self.notify(&frame::start(jpeg.len() as u32, number, recording)) {
            return false;
        }

        let mtu = BLEDevice::take().get_server().connections().next().map(|desc| desc.mtu())
            .unwrap_or_else(|| self.mtu.load(Ordering::SeqCst));

        for piece in jpeg.chunks(frame::piece_size(mtu)) {
            if !self.notify(&frame::data(piece)) {
                return false;
            }
        }

        self.notify(&frame::end(protocol::crc32(jpeg)))
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

fn say(status: &Arc<Mutex<BLECharacteristic>>, message: update::Status) {
    let text = message.text();

    println!("update: {text}");
    status.lock().set_value(text.as_bytes()).notify();
}

fn fail(status: &Arc<Mutex<BLECharacteristic>>, reason: impl Into<String>) {
    say(status, update::Status::Error(reason.into()));
}

fn abandon(update: &mut Option<Update>) {
    if let Some(current) = update.take() {
        unsafe { sys::esp_ota_abort(current.handle) };
    }
}

fn begin(size: u32, crc: u32) -> Result<Update, String> {
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
        match update::parse(&message) {
            Some(update::Packet::Begin { size, crc }) => {
                abandon(&mut update);
                updating.store(true, Ordering::SeqCst);
                progress.store(0, Ordering::SeqCst);

                match begin(size, crc) {
                    Ok(started) => {
                        update = Some(started);
                        say(&status, update::Status::Ready);
                    }
                    Err(reason) => {
                        updating.store(false, Ordering::SeqCst);
                        fail(&status, reason);
                    }
                }
            }
            Some(update::Packet::Data(payload)) => {
                let Some(current) = update.as_mut() else { continue };

                if current.received + payload.len() as u32 > current.size {
                    abandon(&mut update);
                    updating.store(false, Ordering::SeqCst);
                    fail(&status, "too many bytes");
                    continue;
                }

                let result = unsafe { sys::esp_ota_write(current.handle, payload.as_ptr() as _, payload.len()) };
                if result != sys::ESP_OK {
                    abandon(&mut update);
                    updating.store(false, Ordering::SeqCst);
                    fail(&status, format!("write failed {result}"));
                    continue;
                }

                current.received += payload.len() as u32;
                current.running_crc = protocol::crc32_continue(current.running_crc, payload);
                progress.store((current.received as u64 * 100 / current.size.max(1) as u64) as u8, Ordering::SeqCst);

                if current.received / update::STEP as u32 > current.reported {
                    current.reported = current.received / update::STEP as u32;
                    say(&status, update::Status::Got(current.received));
                }
            }
            Some(update::Packet::End) => {
                let Some(current) = update.take() else { continue };

                match finish(current) {
                    Ok(()) => {
                        say(&status, update::Status::Done);
                        thread::sleep(Duration::from_millis(500));
                        unsafe { sys::esp_restart() };
                    }
                    Err(reason) => {
                        updating.store(false, Ordering::SeqCst);
                        fail(&status, reason);
                    }
                }
            }
            Some(update::Packet::Cancel) => {
                abandon(&mut update);
                updating.store(false, Ordering::SeqCst);
                fail(&status, "cancelled");
            }
            None => {}
        }
    }
}

pub fn keep_this_firmware() {
    unsafe { sys::esp_ota_mark_app_valid_cancel_rollback() };
}
