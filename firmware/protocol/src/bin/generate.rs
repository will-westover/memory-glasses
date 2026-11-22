//! Writes the Swift constants the app uses and the byte-level cases both sides are checked against.

use std::fs;
use std::path::Path;

use protocol::{ack, auth, crc32, frame, update};

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn swift() -> String {
    let uuids = [("service", protocol::SERVICE_UUID), ("frame", protocol::FRAME_UUID), ("settings", protocol::SETTINGS_UUID),
                 ("ack", protocol::ACK_UUID), ("update", protocol::UPDATE_UUID), ("updateStatus", protocol::UPDATE_STATUS_UUID),
                 ("version", protocol::VERSION_UUID), ("auth", protocol::AUTH_UUID)];
    let byte_constants = [("frameStart", frame::START), ("frameData", frame::DATA), ("frameEnd", frame::END),
                 ("updateBegin", update::BEGIN), ("updateData", update::DATA), ("updateEnd", update::END),
                 ("updateCancel", update::CANCEL), ("glassesLabel", auth::GLASSES_LABEL), ("phoneLabel", auth::PHONE_LABEL)];
    let sizes = [("recordingBytes", frame::RECORDING_BYTES), ("updateStep", update::STATUS_EVERY_BYTES),
                 ("nonceBytes", auth::NONCE_BYTES), ("shortProofBytes", auth::SHORT_PROOF_BYTES),
                 ("proofBytes", auth::PROOF_BYTES)];
    let strings = [("deviceName", protocol::DEVICE_NAME), ("statusReady", update::READY), ("statusGot", update::RECEIVED),
                 ("statusDone", update::DONE), ("statusError", update::ERROR)];

    let mut lines = vec!["enum GlassesProtocol {".to_string()];
    lines.extend(uuids.iter().map(|(name, value)| format!("    static let {name}UUID = \"{}\"", value.to_uppercase())));
    lines.push(String::new());
    lines.extend(byte_constants.iter().map(|(name, value)| format!("    static let {name}: UInt8 = {value}")));
    lines.push(String::new());
    lines.extend(sizes.iter().map(|(name, value)| format!("    static let {name} = {value}")));
    lines.push(String::new());
    lines.extend(strings.iter().map(|(name, value)| format!("    static let {name} = \"{value}\"")));
    lines.push("}".to_string());

    lines.join("\n") + "\n"
}

fn test_vectors() -> String {
    let jpeg: Vec<u8> = (0u8..40).collect();
    let (phone, glasses): (Vec<u8>, Vec<u8>) = ((1u8..=16).collect(), (101u8..=116).collect());
    let token = "example-token";
    let statuses = [update::Status::Ready, update::Status::Received(65_536), update::Status::Done,
                    update::Status::Error("crc mismatch".to_string())];
    let status_lines: Vec<String> = statuses.iter().map(|status| match status {
        update::Status::Ready => format!("{{\"text\": \"{}\", \"kind\": \"ready\"}}", status.text()),
        update::Status::Received(bytes) => format!("{{\"text\": \"{}\", \"kind\": \"got\", \"bytes\": {bytes}}}", status.text()),
        update::Status::Done => format!("{{\"text\": \"{}\", \"kind\": \"done\"}}", status.text()),
        update::Status::Error(reason) => format!("{{\"text\": \"{}\", \"kind\": \"error\", \"reason\": \"{reason}\"}}", status.text()),
    }).collect();

    format!(
        r#"{{
  "crc32": {{"data": "{}", "crc": {}}},
  "frame": {{
    "length": 70000, "number": 12, "recording": "0123456789abcdef",
    "start": "{}", "piece": "{}", "data": "{}", "crc": {}, "end": "{}"
  }},
  "ack": {{"number": 12, "packet": "{}"}},
  "update": {{
    "size": 1500000, "crc": 305419896, "begin": "{}", "piece": "{}", "data": "{}", "end": "{}", "cancel": "{}",
    "step": {}
  }},
  "statuses": [
    {}
  ],
  "auth": {{
    "token": "{token}", "phone": "{}", "glasses": "{}", "answer": "{}", "reply": "{}"
  }}
}}
"#,
        hex(&jpeg), crc32(&jpeg),
        hex(&frame::start(70_000, 12, "0123456789abcdef")), hex(&jpeg), hex(&frame::data(&jpeg)), crc32(&jpeg),
        hex(&frame::end(crc32(&jpeg))),
        hex(&ack(12)),
        hex(&update::begin(1_500_000, 0x1234_5678)), hex(&jpeg), hex(&update::data(&jpeg)), hex(&[update::END]),
        hex(&[update::CANCEL]), update::STATUS_EVERY_BYTES,
        status_lines.join(",\n    "),
        hex(&phone), hex(&glasses), hex(&auth::glasses_challenge_answer(token, &phone, &glasses)),
        hex(&auth::proof(token, auth::PHONE_LABEL, &phone, &glasses)),
    )
}

fn main() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let swift_path = manifest_dir.join("../../app/MemoryGlasses/Glasses/GlassesProtocol.swift");

    fs::write(&swift_path, swift()).expect("write the Swift constants");
    fs::write(manifest_dir.join("vectors.json"), test_vectors()).expect("write the test cases");

    println!("wrote {} and vectors.json", swift_path.display());
}
