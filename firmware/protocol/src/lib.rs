//! The Bluetooth protocol between the glasses and the phone, defined once.

use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;

pub const DEVICE_NAME: &str = "Memory Glasses";
pub const SERVICE_UUID: &str = "6e1f0001-6d65-6d6f-7279-676c61737365";
pub const FRAME_UUID: &str = "6e1f0002-6d65-6d6f-7279-676c61737365";
pub const SETTINGS_UUID: &str = "6e1f0003-6d65-6d6f-7279-676c61737365";
pub const ACK_UUID: &str = "6e1f0004-6d65-6d6f-7279-676c61737365";
pub const UPDATE_UUID: &str = "6e1f0005-6d65-6d6f-7279-676c61737365";
pub const UPDATE_STATUS_UUID: &str = "6e1f0006-6d65-6d6f-7279-676c61737365";
pub const VERSION_UUID: &str = "6e1f0007-6d65-6d6f-7279-676c61737365";
pub const AUTH_UUID: &str = "6e1f0008-6d65-6d6f-7279-676c61737365";

pub const SETTINGS_KEYS: [&str; 11] = [
    "recording", "frames_per_minute", "display_scale", "display_x", "display_y", "display_brightness",
    "display_large_text", "display_mirror", "display_flip", "display_pattern", "status",
];

pub fn crc32(data: &[u8]) -> u32 {
    crc32_continue(0, data)
}

pub fn crc32_continue(crc: u32, data: &[u8]) -> u32 {
    let mut crc = !crc;

    for &byte in data {
        crc ^= byte as u32;

        for _ in 0..8 {
            crc = if crc & 1 != 0 { (crc >> 1) ^ 0xEDB8_8320 } else { crc >> 1 };
        }
    }

    !crc
}

pub fn read_u32_le(bytes: &[u8]) -> Option<u32> {
    Some(u32::from_le_bytes(bytes.get(..4)?.try_into().ok()?))
}

pub fn ack(number: u32) -> [u8; 4] {
    number.to_le_bytes()
}

pub mod frame {
    use super::read_u32_le;

    pub const START: u8 = 1;
    pub const DATA: u8 = 2;
    pub const END: u8 = 3;
    pub const RECORDING_BYTES: usize = 16;
    pub const SMALLEST_PIECE: usize = 19;

    #[derive(Debug, PartialEq)]
    pub enum Packet<'a> {
        Start { length: u32, number: u32, recording_id: &'a str },
        Data(&'a [u8]),
        End { crc: u32 },
    }

    pub fn start(length: u32, number: u32, recording_id: &str) -> Vec<u8> {
        let mut packet = vec![START];
        packet.extend_from_slice(&length.to_le_bytes());
        packet.extend_from_slice(&number.to_le_bytes());
        packet.extend_from_slice(&recording_id.as_bytes()[..recording_id.len().min(RECORDING_BYTES)]);

        packet
    }

    pub fn data(piece: &[u8]) -> Vec<u8> {
        let mut packet = Vec::with_capacity(piece.len() + 1);
        packet.push(DATA);
        packet.extend_from_slice(piece);

        packet
    }

    pub fn end(crc: u32) -> Vec<u8> {
        let mut packet = vec![END];
        packet.extend_from_slice(&crc.to_le_bytes());

        packet
    }

    pub fn piece_size(mtu: u16) -> usize {
        (mtu as usize).saturating_sub(4).max(SMALLEST_PIECE)
    }

    pub fn parse(packet: &[u8]) -> Option<Packet<'_>> {
        let (&kind, rest) = packet.split_first()?;

        match kind {
            START => Some(Packet::Start {
                length: read_u32_le(rest)?,
                number: read_u32_le(rest.get(4..)?)?,
                recording_id: std::str::from_utf8(rest.get(8..)?).ok()?.trim_end_matches('\0').trim(),
            }),
            DATA => Some(Packet::Data(rest)),
            END => Some(Packet::End { crc: read_u32_le(rest)? }),
            _ => None,
        }
    }
}

pub mod update {
    use super::read_u32_le;

    pub const BEGIN: u8 = 1;
    pub const DATA: u8 = 2;
    pub const END: u8 = 3;
    pub const CANCEL: u8 = 4;
    pub const STATUS_EVERY_BYTES: usize = 32 * 1024;

    #[derive(Debug, PartialEq)]
    pub enum Packet<'a> {
        Begin { size: u32, crc: u32 },
        Data(&'a [u8]),
        End,
        Cancel,
    }

    pub fn begin(size: u32, crc: u32) -> Vec<u8> {
        let mut packet = vec![BEGIN];
        packet.extend_from_slice(&size.to_le_bytes());
        packet.extend_from_slice(&crc.to_le_bytes());

        packet
    }

    pub fn data(piece: &[u8]) -> Vec<u8> {
        let mut packet = Vec::with_capacity(piece.len() + 1);
        packet.push(DATA);
        packet.extend_from_slice(piece);

        packet
    }

    pub fn parse(packet: &[u8]) -> Option<Packet<'_>> {
        let (&kind, rest) = packet.split_first()?;

        match kind {
            BEGIN => Some(Packet::Begin { size: read_u32_le(rest)?, crc: read_u32_le(rest.get(4..)?)? }),
            DATA => Some(Packet::Data(rest)),
            END => Some(Packet::End),
            CANCEL => Some(Packet::Cancel),
            _ => None,
        }
    }

    #[derive(Debug, PartialEq)]
    pub enum Status {
        Ready,
        Received(u32),
        Done,
        Error(String),
    }

    pub const READY: &str = "ready";
    pub const RECEIVED: &str = "got ";
    pub const DONE: &str = "done";
    pub const ERROR: &str = "error";

    impl Status {
        pub fn text(&self) -> String {
            match self {
                Status::Ready => READY.to_string(),
                Status::Received(bytes) => format!("{RECEIVED}{bytes}"),
                Status::Done => DONE.to_string(),
                Status::Error(reason) => format!("{ERROR} {reason}"),
            }
        }

        pub fn parse(text: &str) -> Option<Status> {
            let text = text.trim();

            if text == READY {
                Some(Status::Ready)
            } else if text == DONE {
                Some(Status::Done)
            } else if let Some(bytes) = text.strip_prefix(RECEIVED) {
                bytes.parse().ok().map(Status::Received)
            } else {
                text.strip_prefix(ERROR).map(|reason| Status::Error(reason.trim().to_string()))
            }
        }
    }
}

pub mod auth {
    use super::{Hmac, KeyInit, Mac, Sha256};

    pub const NONCE_BYTES: usize = 16;
    pub const SHORT_PROOF_BYTES: usize = 16;
    pub const PROOF_BYTES: usize = 32;
    pub const GLASSES_LABEL: u8 = b'g';
    pub const PHONE_LABEL: u8 = b'p';

    pub fn proof(token: &str, label: u8, phone_nonce: &[u8], glasses_nonce: &[u8]) -> [u8; PROOF_BYTES] {
        let mut mac = Hmac::<Sha256>::new_from_slice(token.as_bytes()).expect("any key length works");
        mac.update(&[label]);
        mac.update(phone_nonce);
        mac.update(glasses_nonce);

        mac.finalize().into_bytes().into()
    }

    pub fn glasses_challenge_answer(token: &str, phone_nonce: &[u8], glasses_nonce: &[u8]) -> Vec<u8> {
        [glasses_nonce, &proof(token, GLASSES_LABEL, phone_nonce, glasses_nonce)[..SHORT_PROOF_BYTES]].concat()
    }

    pub fn verify_phone_proof(token: &str, phone_nonce: &[u8], glasses_nonce: &[u8], phone_proof: &[u8]) -> bool {
        let expected = proof(token, PHONE_LABEL, phone_nonce, glasses_nonce);

        phone_proof.len() == PROOF_BYTES && expected.iter().zip(phone_proof).fold(0u8, |sum, (ours, theirs)| sum | (ours ^ theirs)) == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc_matches_the_standard_check_value() {
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926);
        assert_eq!(crc32_continue(crc32(b"1234"), b"56789"), 0xCBF4_3926);
    }

    #[test]
    fn frame_packets_survive_a_round_trip() {
        let start = frame::start(70_000, 12, "0123456789abcdef");

        assert_eq!(start.len(), 25);
        assert_eq!(frame::parse(&start), Some(frame::Packet::Start { length: 70_000, number: 12, recording_id: "0123456789abcdef" }));
        assert_eq!(frame::parse(&frame::data(&[9, 8, 7])), Some(frame::Packet::Data(&[9, 8, 7])));
        assert_eq!(frame::parse(&frame::end(0xDEAD_BEEF)), Some(frame::Packet::End { crc: 0xDEAD_BEEF }));
        assert_eq!(frame::parse(&[1, 2, 3]), None);
    }

    #[test]
    fn pieces_fit_the_link() {
        assert_eq!(frame::piece_size(23), 19);
        assert_eq!(frame::piece_size(517), 513);
    }

    #[test]
    fn update_packets_and_statuses_survive_a_round_trip() {
        assert_eq!(update::parse(&update::begin(1_500_000, 7)), Some(update::Packet::Begin { size: 1_500_000, crc: 7 }));
        assert_eq!(update::parse(&[update::CANCEL]), Some(update::Packet::Cancel));

        for status in [update::Status::Ready, update::Status::Received(65_536), update::Status::Done,
                       update::Status::Error("crc mismatch".to_string())] {
            assert_eq!(update::Status::parse(&status.text()), Some(status));
        }
    }

    #[test]
    fn only_the_right_token_passes_the_handshake() {
        let (phone, glasses) = ([1u8; 16], [2u8; 16]);
        let reply = auth::proof("secret", auth::PHONE_LABEL, &phone, &glasses);

        assert!(auth::verify_phone_proof("secret", &phone, &glasses, &reply));
        assert!(!auth::verify_phone_proof("other", &phone, &glasses, &reply));
        assert!(!auth::verify_phone_proof("secret", &phone, &glasses, &reply[..16]));
        assert_eq!(auth::glasses_challenge_answer("secret", &phone, &glasses).len(), 32);
    }
}
