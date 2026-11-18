import SwiftUI

struct GlassesView: View {
    @AppStorage("serverAddress") private var address = API.defaultAddress
    @AppStorage("serverToken") private var token = ""

    @State private var framesPerMinute = 30.0
    @State private var displayScale = 1.0
    @State private var status = "Checking…"
    @State private var connected = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(rateLabel)
                            .font(.headline)

                        Slider(value: $framesPerMinute, in: 0...60, step: 1) { editing in
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
                    VStack(alignment: .leading, spacing: 8) {
                        Text("\(Int((displayScale * 100).rounded()))% of the screen")
                            .font(.headline)

                        Slider(value: $displayScale, in: 0.3...1, step: 0.05) { editing in
                            if !editing { Task { await save() } }
                        }
                        .disabled(!connected)
                        .accessibilityLabel("Display size")
                    }
                } header: {
                    Text("Display")
                } footer: {
                    Text("Looking through the lens as you would wearing them, make this smaller until you can see all four edges of the box. The glasses show the box for a few seconds after a change.")
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
        }
    }

    private var rateLabel: String {
        framesPerMinute == 0 ? "Sound only, no pictures" : "\(Int(framesPerMinute)) pictures a minute"
    }

    private func load() async {
        do {
            let settings: GlassesSettings = try await API.current.get("/settings")

            framesPerMinute = settings.framesPerMinute
            displayScale = settings.displayScale
            connected = true
            status = "Connected"
        } catch {
            connected = false
            status = error.localizedDescription
        }
    }

    private func save() async {
        do {
            let saved: GlassesSettings = try await API.current.send(
                "/settings", method: "PUT", body: GlassesSettings(framesPerMinute: framesPerMinute, displayScale: displayScale)
            )

            framesPerMinute = saved.framesPerMinute
            displayScale = saved.displayScale
            status = "Saved"
        } catch {
            status = error.localizedDescription
        }
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
