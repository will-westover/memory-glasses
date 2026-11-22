import SwiftUI

struct GlassesView: View {
    @AppStorage("serverAddress") private var address = API.defaultAddress
    @AppStorage("serverToken") private var token = API.defaultToken

    @State private var settings = GlassesSettings()
    @State private var glassesStatus: GlassesStatus?
    @State private var serverMessage = "Checking…"
    @State private var serverReachable = false
    @State private var people: [Person] = []
    @State private var places: [Place] = []
    @State private var captionCost: Double?

    private let link = GlassesLink.shared
    private static let presets: [(name: String, rate: Double)] = [("Light", 10), ("Normal", 30), ("Detailed", 60)]

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    GlassesStatusCard(activity: activity, detail: activityDetail, photos: photos, lastUpload: link.lastUpload,
                                      canRecord: serverReachable, recording: $settings.recording)
                }

                Section {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text(rateLabel)
                                .font(.headline)

                            Spacer()

                            if let hourlyCost {
                                Text(hourlyCost)
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                            }
                        }

                        HStack(spacing: 8) {
                            ForEach(Self.presets, id: \.name) { preset in
                                Button(preset.name) {
                                    settings.framesPerMinute = preset.rate
                                    Task { await save() }
                                }
                                .buttonStyle(.bordered)
                                .tint(settings.framesPerMinute == preset.rate ? .accentColor : .secondary)
                            }
                        }

                        Slider(value: $settings.framesPerMinute, in: 0...60, step: 1) { editing in
                            if !editing { Task { await save() } }
                        }
                        .accessibilityLabel("Pictures a minute")
                    }
                    .disabled(!serverReachable)
                } header: {
                    InfoHeader(title: "Camera", text: "How often the glasses take a picture. More pictures remember more and cost more to describe. The cost shown is the most an hour of recording would cost; pictures that look the same as the last one are skipped.")
                }

                Section {
                    HStack(spacing: 14) {
                        GlassesDisplayPreview(settings: settings, lines: displayLines)
                            .frame(width: 96)

                        VStack(alignment: .leading, spacing: 2) {
                            Text("Showing now")
                                .font(.subheadline.weight(.medium))

                            Text(link.state == .connected ? displayLines.joined(separator: " · ") : "Changes apply when the glasses connect")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }

                    NavigationLink {
                        DisplaySetup(settings: $settings, save: save)
                    } label: {
                        Label("Display settings", systemImage: "slider.horizontal.3")
                    }
                    .disabled(!serverReachable)
                } header: {
                    InfoHeader(title: "Display", text: "The small screen inside the glasses. Set its focus pattern, size, position, text size, brightness and which way round the picture is.")
                }

                Section {
                    NavigationLink {
                        PeopleView()
                    } label: {
                        LabeledContent { Text(peopleCount) } label: { Label("People", systemImage: "person.2") }
                    }

                    NavigationLink {
                        PlacesView()
                    } label: {
                        LabeledContent { Text(placesCount) } label: { Label("Places and rooms", systemImage: "house") }
                    }
                } header: {
                    InfoHeader(title: "People and places", text: "Name the people, places and rooms the glasses have seen, so answers can say who was there and where.")
                }

                Section {
                    NavigationLink {
                        ServerSetup(address: $address, token: $token, message: serverMessage, reachable: serverReachable) { await load() }
                    } label: {
                        LabeledContent {
                            Text(serverReachable ? "Connected" : "Not reachable")
                                .foregroundStyle(serverReachable ? .green : .red)
                        } label: {
                            Label("Server", systemImage: "server.rack")
                        }
                    }
                }
            }
            .navigationTitle("Glasses")
            .task(id: address + token) { await load() }
            .task(id: address + token) { await watchGlasses() }
            .task { await GlassesLink.shared.checkForUpdate() }
            .onChange(of: settings.recording) { Task { await save() } }
        }
    }

    private var activity: GlassesActivity {
        if let progress = link.updateProgress, progress < 1 { return .updating }
        if link.state != .connected && glassesStatus?.sending != true { return .disconnected }
        if !settings.recording { return .paused }

        return glassesStatus?.sending == true ? .recording : .waiting
    }

    private var activityDetail: String {
        switch activity {
        case .updating:
            return "Keep the glasses close to the phone."
        case .recording:
            return "\(Int(settings.framesPerMinute)) pictures a minute"
        case .waiting:
            return "Connected. Waiting for the first photo."
        case .paused:
            return "Turn recording on to start a new moment."
        case .disconnected:
            if let processing = glassesStatus?.processing, processing > 0 {
                return processing == 1 ? "Processing a moment" : "Processing \(processing) moments"
            }

            return link.state.label
        }
    }

    private var photos: Int {
        glassesStatus?.sending == true ? glassesStatus?.photos ?? 0 : link.photosSent
    }

    private var displayLines: [String] {
        if settings.displayPattern { return ["Aa 0123"] }

        let text = link.glassesDisplayText.isEmpty ? (settings.recording ? "Rec \(photos)" : "Paused") : link.glassesDisplayText

        return ["BT", text]
    }

    private var rateLabel: String {
        settings.framesPerMinute == 0 ? "No pictures" : "\(Int(settings.framesPerMinute)) pictures a minute"
    }

    private var hourlyCost: String? {
        guard let captionCost, settings.framesPerMinute > 0 else { return nil }

        return "up to \(dollars(captionCost * settings.framesPerMinute * 60)) an hour"
    }

    private var peopleCount: String {
        let named = people.filter { $0.name != nil }.count

        return people.isEmpty ? "" : "\(named) of \(people.count) named"
    }

    private var placesCount: String {
        places.isEmpty ? "" : "\(places.count)"
    }

    private func watchGlasses() async {
        while !Task.isCancelled {
            glassesStatus = try? await API.current.get("/glasses/status")
            try? await Task.sleep(for: .seconds(5))
        }
    }

    private func load() async {
        do {
            settings = try await API.current.get("/settings")
            serverReachable = true
            serverMessage = "Connected"
        } catch {
            serverReachable = false
            serverMessage = error.localizedDescription
            return
        }

        people = (try? await API.current.get("/people")) ?? []
        places = (try? await API.current.get("/places")) ?? []

        if let stats: Stats = try? await API.current.get("/stats"),
           let caption = stats.costs.first(where: { $0.step == "caption" }), caption.calls > 0 {
            captionCost = caption.usd / Double(caption.calls)
        }
    }

    private func save() async {
        do {
            settings = try await API.current.send("/settings", method: "PUT", body: settings)
            link.applySettings(settings)
            serverMessage = "Saved"
        } catch {
            serverMessage = error.localizedDescription
        }
    }
}

struct ServerSetup: View {
    @Binding var address: String
    @Binding var token: String
    let message: String
    let reachable: Bool
    let check: () async -> Void

    var body: some View {
        Form {
            Section {
                TextField("Address", text: $address)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()

                SecureField("Token", text: $token)
            } header: {
                InfoHeader(title: "Server", text: "The address and token of your Memory Glasses server. The app comes with these built in; change them only if the server moves.")
            }

            Section {
                Label(message, systemImage: reachable ? "checkmark.circle.fill" : "xmark.circle")
                    .foregroundStyle(reachable ? .green : .secondary)

                Button("Check connection") { Task { await check() } }
            }
        }
        .navigationTitle("Server")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct DisplaySetup: View {
    @Binding var settings: GlassesSettings
    let save: () async -> Void

    var body: some View {
        Form {
            Section {
                GlassesDisplayPreview(settings: settings, lines: settings.displayPattern ? ["Aa 0123"] : ["BT", "Rec 12"])
                    .frame(maxWidth: 220)
                    .frame(maxWidth: .infinity)
                    .listRowBackground(Color.clear)
            }

            Section {
                Toggle("Show focus pattern", isOn: $settings.displayPattern)
            } header: {
                InfoHeader(title: "Focus", text: "Shows fine lines and sample text on the glasses. With your eye where it sits when you wear them, swap the focus spacers until the lines look sharpest.")
            }

            Section {
                slider("Size", value: $settings.displayScale, in: 0.3...1, step: 0.05,
                       shown: "\(percent(settings.displayScale)) of the screen")
                slider("Left and right", value: $settings.displayX, in: -1...1, step: 0.1,
                       shown: offset(settings.displayX, negative: "left", positive: "right"))
                slider("Up and down", value: $settings.displayY, in: -1...1, step: 0.1,
                       shown: offset(settings.displayY, negative: "up", positive: "down"))
            } header: {
                InfoHeader(title: "Size and position", text: "Shrink the box until you can see all four edges, then move it to where the lens shows it clearest. The glasses outline the box for a few seconds after each change.")
            }

            Section {
                Toggle("Large text", isOn: $settings.displayLargeText)
                slider("Brightness", value: $settings.displayBrightness, in: 0.05...1, step: 0.05,
                       shown: percent(settings.displayBrightness))
            } header: {
                InfoHeader(title: "Text", text: "Large text fits fewer words but is easier to read through the lens.")
            }

            Section {
                Toggle("Mirror left to right", isOn: $settings.displayMirror)
                Toggle("Flip upside down", isOn: $settings.displayFlip)
            } header: {
                InfoHeader(title: "Which way round", text: "Turn on mirroring if the display is seen in a mirror or beam-splitter cube, so text reads the right way round.")
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
