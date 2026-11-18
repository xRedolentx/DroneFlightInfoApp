// Local keychain decoder for Flight Desk.
//
// The DJI developer key never reaches this process. Flight Desk fetches the
// keychain over its official exchange, caches it with DPAPI, and sends only
// {file, keychains} on stdin. The file path and keychains are not printed.
use dji_log_parser::keychain::KeychainFeaturePoint;
use dji_log_parser::DJILog;
use serde::Deserialize;
use std::io::{Read, Write};

#[derive(Deserialize)]
struct Request {
    file: String,
    keychains: Vec<Vec<KeychainFeaturePoint>>,
}

fn main() {
    let mut input = String::new();
    if std::io::stdin().read_to_string(&mut input).is_err() {
        std::process::exit(2);
    }
    let req: Request = match serde_json::from_str(&input) {
        Ok(v) => v,
        Err(_) => std::process::exit(3),
    };
    let bytes = match std::fs::read(&req.file) {
        Ok(v) => v,
        Err(_) => std::process::exit(4),
    };
    let parser = match DJILog::from_bytes(bytes) {
        Ok(v) => v,
        Err(_) => std::process::exit(5),
    };
    let records = match parser.records(Some(req.keychains.clone())) {
        Ok(v) => v,
        Err(_) => std::process::exit(6),
    };
    let frames = match parser.frames(Some(req.keychains)) {
        Ok(v) => v,
        Err(_) => std::process::exit(7),
    };
    let details: dji_log_parser::frame::FrameDetails = parser.details.into();
    let out = serde_json::json!({
        "version": parser.version,
        "details": details,
        "frames": frames,
        "recordCount": records.len()
    });
    let text = match serde_json::to_string(&out) {
        Ok(v) => v,
        Err(_) => std::process::exit(8),
    };
    let mut stdout = std::io::stdout().lock();
    if stdout.write_all(text.as_bytes()).is_err() {
        std::process::exit(9);
    }
}
