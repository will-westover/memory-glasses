import SwiftUI

struct GlassesView: View {
    @AppStorage("serverAddress") private var address = API.defaultAddress
    @AppStorage("serverToken") private var token = ""

    @State private var framesPerMinute = 30.0
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
                    NavigationLink {
                        PeopleView()
                    } label: {
                        Label("People", systemImage: "person.2")
                    }
                } footer: {
                    Text("Name the people the glasses have seen, so answers can say who was there.")
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
                }
            }
            .navigationTitle("Glasses")
            .task { await load() }
        }
    }

    private var rateLabel: String {
        framesPerMinute == 0 ? "Sound only, no pictures" : "\(Int(framesPerMinute)) pictures a minute"
    }

    private func load() async {
        do {
            let settings: GlassesSettings = try await API.current.get("/settings")

            framesPerMinute = settings.framesPerMinute
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
                "/settings", method: "PUT", body: GlassesSettings(framesPerMinute: framesPerMinute)
            )

            framesPerMinute = saved.framesPerMinute
            status = "Saved"
        } catch {
            status = error.localizedDescription
        }
    }
}
