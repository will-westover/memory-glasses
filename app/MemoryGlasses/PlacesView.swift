import SwiftUI

struct PlacesView: View {
    @State private var spaces: [Space] = []
    @State private var loaded = false
    @State private var problem: String?

    var body: some View {
        Group {
            if let problem {
                ServerProblem(message: problem, retry: load)
            } else if loaded && spaces.isEmpty {
                ContentUnavailableView("No places yet", systemImage: "house",
                                       description: Text("Places and their rooms appear here after a recording is processed."))
            } else {
                List(spaces) { space in
                    NavigationLink {
                        PlaceDetail(space: space)
                    } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(space.label)
                                .font(.body.weight(.medium))

                            Text(space.rooms.isEmpty ? momentCount(space.moments) : space.rooms.joined(separator: ", "))
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
            spaces = try await API.current.get("/spaces")
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
    let space: Space

    @State private var name = ""
    @State private var detail: SpaceDetail?
    @State private var status: String?

    var body: some View {
        Form {
            Section {
                TextField("Name", text: $name)
                    .textInputAutocapitalization(.words)
                    .onSubmit { Task { await saveName() } }

                Button("Save name") { Task { await saveName() } }
                    .disabled(name.trimmingCharacters(in: .whitespaces) == (space.name ?? ""))
            } header: {
                Text("Place")
            } footer: {
                Text(status ?? "\(momentCount(space.moments)) recorded here.")
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
                    Text("Rooms")
                } footer: {
                    Text("Every frame is placed in a room from what was seen, said and where the phone was.")
                }
            }
        }
        .navigationTitle(space.label)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { name = space.name ?? "" }
        .task { detail = try? await API.current.get("/spaces/\(space.id)") }
    }

    private func saveName() async {
        let trimmed = name.trimmingCharacters(in: .whitespaces)

        do {
            let _: SpaceTag = try await API.current.send("/spaces/\(space.id)", method: "PUT",
                                                        body: PersonName(name: trimmed.isEmpty ? nil : trimmed))
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
            let _: SpaceTag = try await API.current.send("/rooms/\(room.id)", method: "PUT",
                                                        body: PersonName(name: name.trimmingCharacters(in: .whitespaces)))
            status = "Saved."
        } catch {
            status = error.localizedDescription
        }
    }
}
