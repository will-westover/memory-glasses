const SETTINGS: [&str; 4] = ["wifi_name", "wifi_password", "server", "token"];

fn main() {
    embuild::espidf::sysenv::output();

    println!("cargo:rerun-if-changed=glasses.toml");
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
}
