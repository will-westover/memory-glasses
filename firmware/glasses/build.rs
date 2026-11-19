const SETTINGS: [&str; 4] = ["wifi_name", "wifi_password", "server", "token"];

fn main() {
    embuild::espidf::sysenv::output();

    println!("cargo:rerun-if-changed=glasses.toml");
    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=../../.git/HEAD");
    let text = std::fs::read_to_string("glasses.toml").unwrap_or_default();

    for key in SETTINGS {
        let value = text
            .lines()
            .filter_map(|line| line.split_once('='))
            .find(|(name, _)| name.trim() == key)
            .map(|(_, value)| value.trim().trim_matches('"').to_string())
            .unwrap_or_default();

        println!("cargo:rustc-env=GLASSES_{}={}", key.to_uppercase(), value);
    }

    let commit = std::process::Command::new("git").args(["rev-parse", "--short", "HEAD"]).output().ok()
        .map(|out| String::from_utf8_lossy(&out.stdout).trim().to_string())
        .unwrap_or_default();
    let stamp = std::process::Command::new("date").args(["-u", "+%Y%m%d-%H%M"]).output().ok()
        .map(|out| String::from_utf8_lossy(&out.stdout).trim().to_string())
        .unwrap_or_default();

    println!("cargo:rustc-env=GLASSES_VERSION={stamp}-{commit}");
}
