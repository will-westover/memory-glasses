import SwiftUI

struct Person: Decodable, Identifiable, Hashable {
    let id: Int
    let name: String?
    let faces: Int
    let firstSeen: Date
    let lastSeen: Date
    let face: String?

    var label: String { name ?? "Unnamed person" }
}

struct PersonTag: Codable, Hashable {
    let id: Int
    let name: String?
    let face: String?
}

struct NameUpdate: Encodable {
    let name: String?
}

struct MergeRequest: Encodable {
    let into: Int
}

struct EmptyReply: Decodable {}

struct FaceImage: View {
    let path: String?
    var size: CGFloat = 48

    var body: some View {
        FrameImage(path: path)
            .frame(width: size, height: size)
            .clipShape(Circle())
    }
}

struct PeopleView: View {
    @State private var people: [Person] = []
    @State private var problem: String?
    @State private var loaded = false

    var body: some View {
        Group {
            if let problem {
                ServerProblem(message: problem, retry: load)
            } else if !loaded {
                ProgressView()
            } else if people.isEmpty {
                ContentUnavailableView("No faces yet", systemImage: "person.crop.circle",
                                       description: Text("Run the faces step on a recording."))
            } else {
                List(people) { person in
                    NavigationLink {
                        PersonView(person: person, others: people.filter { $0.id != person.id })
                    } label: {
                        HStack(spacing: 14) {
                            FaceImage(path: person.face)

                            VStack(alignment: .leading, spacing: 3) {
                                Text(person.label)
                                    .font(.body.weight(.medium))
                                    .foregroundStyle(person.name == nil ? .secondary : .primary)

                                Text("Seen \(person.faces) times · last \(person.lastSeen.formatted(.dateTime.weekday(.abbreviated).hour().minute()))")
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                }
                .listStyle(.plain)
                .refreshable { await load() }
            }
        }
        .navigationTitle("People")
        .task { await load() }
    }

    private func load() async {
        do {
            people = try await API.current.get("/people")
            problem = nil
        } catch {
            problem = error.localizedDescription
        }

        loaded = true
    }
}

struct PersonView: View {
    let person: Person
    let others: [Person]

    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var status: String?
    @State private var confirmingForget = false
    @State private var working = false

    var body: some View {
        Form {
            Section {
                HStack {
                    Spacer()
                    FaceImage(path: person.face, size: 140)
                    Spacer()
                }
                .listRowBackground(Color.clear)
            }

            Section {
                TextField("Name", text: $name)
                    .textInputAutocapitalization(.words)
                    .onSubmit { Task { await save() } }

                Button("Save name") { Task { await save() } }
                    .disabled(name.trimmingCharacters(in: .whitespaces) == (person.name ?? ""))
            } header: {
                Text("Name")
            } footer: {
                Text(status ?? "Seen \(person.faces) times, from \(person.firstSeen.formatted(date: .abbreviated, time: .shortened)) to \(person.lastSeen.formatted(date: .abbreviated, time: .shortened)).")
            }

            if !others.isEmpty {
                Section("Same person as") {
                    ForEach(others) { other in
                        Button { Task { await merge(into: other) } } label: {
                            HStack(spacing: 12) {
                                FaceImage(path: other.face, size: 36)
                                Text(other.label)
                            }
                        }
                        .buttonStyle(.plain)
                        .disabled(working)
                    }
                }
            }

            Section {
                Button(role: .destructive) { confirmingForget = true } label: {
                    HStack {
                        Text("Forget this person")

                        if working {
                            Spacer()
                            ProgressView().controlSize(.small)
                        }
                    }
                }
                .disabled(working)
            } footer: {
                Text("Deletes every face of them. Their photos stay.")
            }
        }
        .navigationTitle(person.label)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { name = person.name ?? "" }
        .confirmationDialog("Forget \(person.label)?", isPresented: $confirmingForget, titleVisibility: .visible) {
            Button("Forget", role: .destructive) { Task { await forget() } }
        } message: {
            Text("This deletes all \(person.faces) faces of them. It can't be undone.")
        }
    }

    private func save() async {
        let trimmed = name.trimmingCharacters(in: .whitespaces)

        do {
            let _: EmptyReply = try await API.current.send("/people/\(person.id)", method: "PUT",
                                                   body: NameUpdate(name: trimmed.isEmpty ? nil : trimmed))
            status = "Saved. Answers will use the new name in about a minute."
        } catch {
            status = error.localizedDescription
        }
    }

    private func merge(into other: Person) async {
        working = true
        defer { working = false }

        do {
            let _: EmptyReply = try await API.current.send("/people/\(person.id)/merge", method: "POST", body: MergeRequest(into: other.id))
            dismiss()
        } catch {
            status = error.localizedDescription
        }
    }

    private func forget() async {
        working = true
        defer { working = false }

        do {
            try await API.current.delete("/people/\(person.id)")
            dismiss()
        } catch {
            status = error.localizedDescription
        }
    }
}

struct PeopleChips: View {
    let people: [PersonTag]

    var body: some View {
        if !people.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(people, id: \.self) { person in
                        NavigationLink {
                            PeopleView()
                        } label: {
                            HStack(spacing: 8) {
                                FaceImage(path: person.face, size: 28)

                                Text(person.name ?? "Unnamed person")
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(person.name == nil ? .secondary : .primary)
                            }
                            .padding(.leading, 4)
                            .padding(.trailing, 12)
                            .padding(.vertical, 4)
                            .background(Color(.secondarySystemBackground), in: Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .scrollClipDisabled()
        }
    }
}
