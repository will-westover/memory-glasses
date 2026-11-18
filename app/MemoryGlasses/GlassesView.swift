import SwiftUI

struct GlassesView: View {
    @AppStorage("serverAddress") private var address = API.defaultAddress
    @AppStorage("serverToken") private var token = ""

    @State private var settings = GlassesSettings()
    @State private var glasses: GlassesStatus?
    @State private var status = "Checking…"
    @State private var connected = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Toggle(isOn: $settings.recording) {
                        Label("Recording", systemImage: settings.recording ? "record.circle.fill" : "pause.circle")
                    }
                    .disabled(!connected)

                    Label(glassesLabel, systemImage: glasses?.sending == true ? "dot.radiowaves.up.forward" : "eyeglasses")
                        .foregroundStyle(glasses?.sending == true ? .green : .secondary)
                } header: {
                    Text("Glasses")
                } footer: {
                    Text("While this is off, the glasses take no pictures. Turning it back on starts a new moment.")
                }

                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(rateLabel)
                            .font(.headline)

                        Slider(value: $settings.framesPerMinute, in: 0...60, step: 1) { editing in
                            if !editing { Task { await save() } }
                        }
                        .disabled(!connected)
                        .accessibilityLabel("Pictures a minute")
                    }
                } header: {
                    Text("Camera")
                } footer: {
                    Text("How often the glasses take a picture. More pictures remember more and cost more.")
                }

                Section {
                    NavigationLink {
                        DisplaySetup(settings: $settings, save: save)
                    } label: {
                        Label("Display", systemImage: "eyeglasses")
                    }
                    .disabled(!connected)
                } footer: {
                    Text("Focus pattern, size, position, text size, brightness and which way round the picture is.")
                }

                Section {
                    NavigationLink {
                        PeopleView()
                    } label: {
                        Label("People", systemImage: "person.2")
                    }

                    NavigationLink {
                        PlacesView()
                    } label: {
                        Label("Places and rooms", systemImage: "house")
                    }
                } footer: {
                    Text("Name the people, places and rooms the glasses have seen, so answers can say who was there and where.")
                }

                Section {
                    TextField("Address", text: $address)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    SecureField("Token (if the server needs one)", text: $token)

                    Label(status, systemImage: connected ? "checkmark.circle.fill" : "xmark.circle")
                        .foregroundStyle(connected ? .green : .secondary)

                    Button("Check connection") { Task { await load() } }
                } header: {
                    Text("Server")
                } footer: {
                    Text("To connect quickly, run memory.pair on the Mac and scan its QR code with the iPhone camera.")
                }
            }
            .navigationTitle("Glasses")
            .task(id: address + token) { await load() }
            .task(id: address + token) { await watchGlasses() }
            .onChange(of: settings.recording) { Task { await save() } }
        }
    }

    private var rateLabel: String {
        settings.framesPerMinute == 0 ? "Sound only, no pictures" : "\(Int(settings.framesPerMinute)) pictures a minute"
    }

    private var glassesLabel: String {
        guard let glasses else { return "Checking the glasses…" }

        if glasses.sending {
            return "Sending, \(glasses.photos) pictures in this moment"
        }

        if glasses.processing > 0 {
            return glasses.processing == 1 ? "Processing a moment" : "Processing \(glasses.processing) moments"
        }

        return settings.recording ? "Not sending. Plug them into the iPhone with the hotspot on." : "Paused"
    }

    private func watchGlasses() async {
        while !Task.isCancelled {
            glasses = try? await API.current.get("/glasses/status")
            try? await Task.sleep(for: .seconds(5))
        }
    }

    private func load() async {
        do {
            settings = try await API.current.get("/settings")
            connected = true
            status = "Connected"
        } catch {
            connected = false
            status = error.localizedDescription
        }
    }

    private func save() async {
        do {
            settings = try await API.current.send("/settings", method: "PUT", body: settings)
            status = "Saved"
        } catch {
            status = error.localizedDescription
        }
    }
}

struct DisplaySetup: View {
    @Binding var settings: GlassesSettings
    let save: () async -> Void

    var body: some View {
        Form {
            Section {
                Toggle("Show focus pattern", isOn: $settings.displayPattern)
            } footer: {
                Text("Shows fine lines and sample text on the glasses. With your eye where it sits when you wear them, swap the focus spacers until the lines look sharpest.")
            }

            Section {
                slider("Size", value: $settings.displayScale, in: 0.3...1, step: 0.05,
                       shown: "\(percent(settings.displayScale)) of the screen")
                slider("Left and right", value: $settings.displayX, in: -1...1, step: 0.1,
                       shown: offset(settings.displayX, negative: "left", positive: "right"))
                slider("Up and down", value: $settings.displayY, in: -1...1, step: 0.1,
                       shown: offset(settings.displayY, negative: "up", positive: "down"))
            } header: {
                Text("Size and position")
            } footer: {
                Text("Shrink the box until you can see all four edges, then move it to where the lens shows it clearest. The glasses outline the box for a few seconds after each change.")
            }

            Section {
                Toggle("Large text", isOn: $settings.displayLargeText)
                slider("Brightness", value: $settings.displayBrightness, in: 0.05...1, step: 0.05,
                       shown: percent(settings.displayBrightness))
            } header: {
                Text("Text")
            } footer: {
                Text("Large text fits fewer words but is easier to read through the lens.")
            }

            Section {
                Toggle("Mirror left to right", isOn: $settings.displayMirror)
                Toggle("Flip upside down", isOn: $settings.displayFlip)
            } header: {
                Text("Which way round")
            } footer: {
                Text("The cube reflects the display, so text needs mirroring to read the right way round.")
            }
        }
        .navigationTitle("Display")
        .navigationBarTitleDisplayMode(.inline)
        .onChange(of: settings.displayPattern) { Task { await save() } }
        .onChange(of: settings.displayLargeText) { Task { await save() } }
        .onChange(of: settings.displayMirror) { Task { await save() } }
        .onChange(of: settings.displayFlip) { Task { await save() } }
    }

    private func slider(_ title: String, value: Binding<Double>, in range: ClosedRange<Double>, step: Double,
                        shown: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(title)
                Spacer()
                Text(shown)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }

            Slider(value: value, in: range, step: step) { editing in
                if !editing { Task { await save() } }
            }
            .accessibilityLabel(title)
        }
    }

    private func percent(_ value: Double) -> String {
        "\(Int((value * 100).rounded()))%"
    }

    private func offset(_ value: Double, negative: String, positive: String) -> String {
        let amount = Int((abs(value) * 100).rounded())

        return amount == 0 ? "Centred" : "\(amount)% \(value < 0 ? negative : positive)"
    }
}

struct Pairing: Identifiable {
    let server: String
    let token: String

    var id: String { server }

    init?(url: URL) {
        guard url.scheme == "memoryglasses", url.host() == "pair",
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              let server = items.first(where: { $0.name == "server" })?.value else { return nil }

        self.server = server
        self.token = items.first(where: { $0.name == "token" })?.value ?? ""
    }

    func save() {
        UserDefaults.standard.set(server, forKey: "serverAddress")
        UserDefaults.standard.set(token, forKey: "serverToken")
    }
}
