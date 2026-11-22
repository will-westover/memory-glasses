import SwiftUI

struct PlacesView: View {
    @State private var places: [Place] = []
    @State private var loaded = false
    @State private var problem: String?

    var body: some View {
        Group {
            if let problem {
                ServerProblem(message: problem, retry: load)
            } else if loaded && places.isEmpty {
                ContentUnavailableView("No places yet", systemImage: "house",
                                       description: Text("Places and their rooms appear here after a recording is processed."))
            } else {
                List(places) { place in
                    NavigationLink {
                        PlaceDetail(place: place)
                    } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(place.label)
                                .font(.body.weight(.medium))

                            Text(place.rooms.isEmpty ? momentCount(place.moments) : place.rooms.joined(separator: ", "))
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        .padding(.vertical, 2)
                    }
                }
                .listStyle(.plain)
                .refreshable { await load() }
            }
        }
        .navigationTitle("Places")
        .task { await load() }
    }

    private func load() async {
        do {
            places = try await API.current.get("/places")
            problem = nil
        } catch {
            problem = error.localizedDescription
        }

        loaded = true
    }
}

func momentCount(_ count: Int) -> String {
    count == 1 ? "1 moment" : "\(count) moments"
}

struct PlaceDetail: View {
    let place: Place

    @State private var name = ""
    @State private var detail: PlaceRooms?
    @State private var status: String?

    var body: some View {
        Form {
            Section {
                TextField("Name", text: $name)
                    .textInputAutocapitalization(.words)
                    .onSubmit { Task { await saveName() } }

                Button("Save name") { Task { await saveName() } }
                    .disabled(name.trimmingCharacters(in: .whitespaces) == (place.name ?? ""))
            } header: {
                Text("Place")
            } footer: {
                Text(status ?? "\(momentCount(place.moments)) recorded here.")
            }

            if let detail, !detail.rooms.isEmpty {
                Section {
                    ForEach(detail.rooms) { room in
                        NavigationLink {
                            RoomDetail(room: room)
                        } label: {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(room.name)

                                if let description = room.description {
                                    Text(description)
                                        .font(.subheadline)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                } header: {
                    InfoHeader(title: "Rooms", text: "Every frame is placed in a room from what was seen, said and where the phone was.")
                }
            }
        }
        .navigationTitle(place.label)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { name = place.name ?? "" }
        .task { detail = try? await API.current.get("/places/\(place.id)") }
    }

    private func saveName() async {
        let trimmed = name.trimmingCharacters(in: .whitespaces)

        do {
            let _: PlaceTag = try await API.current.send("/places/\(place.id)", method: "PUT",
                                                        body: NameUpdate(name: trimmed.isEmpty ? nil : trimmed))
            status = "Saved."
        } catch {
            status = error.localizedDescription
        }
    }
}

struct RoomDetail: View {
    let room: RoomInfo

    @State private var name = ""
    @State private var status: String?

    var body: some View {
        Form {
            Section {
                TextField("Name", text: $name)
                    .textInputAutocapitalization(.words)
                    .onSubmit { Task { await save() } }

                Button("Save name") { Task { await save() } }
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || name == room.name)
            } header: {
                Text("Name")
            } footer: {
                Text(status ?? "\(room.frames) frames taken here. Answers use this name.")
            }
        }
        .navigationTitle(room.name)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { name = room.name }
    }

    private func save() async {
        do {
            let _: PlaceTag = try await API.current.send("/rooms/\(room.id)", method: "PUT",
                                                        body: NameUpdate(name: name.trimmingCharacters(in: .whitespaces)))
            status = "Saved."
        } catch {
            status = error.localizedDescription
        }
    }
}
