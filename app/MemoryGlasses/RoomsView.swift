import SwiftUI

struct RoomsView: View {
    @State private var rooms: [Room] = []
    @State private var loaded = false
    @State private var problem: String?

    var body: some View {
        Group {
            if let problem {
                ServerProblem(message: problem, retry: load)
            } else if loaded && rooms.isEmpty {
                ContentUnavailableView("No rooms yet", systemImage: "house",
                                       description: Text("Record a moment on an iPhone and its room is saved here."))
            } else {
                List(rooms) { room in
                    NavigationLink {
                        RoomDetail(room: room)
                    } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(room.label)
                                .font(.body.weight(.medium))

                            Text("\(room.moments) moments · last \(room.updatedAt.formatted(.dateTime.weekday(.abbreviated).hour().minute()))")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 2)
                    }
                }
                .listStyle(.plain)
                .refreshable { await load() }
            }
        }
        .navigationTitle("Rooms")
        .task { await load() }
    }

    private func load() async {
        do {
            rooms = try await API.current.get("/rooms")
            problem = nil
        } catch {
            problem = error.localizedDescription
        }

        loaded = true
    }
}

struct RoomDetail: View {
    let room: Room

    @State private var name = ""
    @State private var moments: [String] = []
    @State private var status: String?

    var body: some View {
        Form {
            Section {
                TextField("Name", text: $name)
                    .textInputAutocapitalization(.words)
                    .onSubmit { Task { await save() } }

                Button("Save name") { Task { await save() } }
                    .disabled(name.trimmingCharacters(in: .whitespaces) == (room.name ?? ""))
            } header: {
                Text("Name")
            } footer: {
                Text(status ?? "Moments recorded here share one map, so their paths line up.")
            }

            Section("Moments here") {
                ForEach(moments, id: \.self) { Text($0) }
            }
        }
        .navigationTitle(room.label)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { name = room.name ?? "" }
        .task { moments = (try? await API.current.get("/rooms/\(room.id)/moments")) ?? [] }
    }

    private func save() async {
        let trimmed = name.trimmingCharacters(in: .whitespaces)

        do {
            let _: RoomTag = try await API.current.send("/rooms/\(room.id)", method: "PUT",
                                                       body: PersonName(name: trimmed.isEmpty ? nil : trimmed))
            status = "Saved."
        } catch {
            status = error.localizedDescription
        }
    }
}
