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

                                Text("\(seenCount(person.faces)) · last \(whenLast(person.lastSeen))")
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

struct PersonDetail: Decodable {
    let photos: [PersonPhoto]
    let recordings: [PersonRecording]
}

struct PersonPhoto: Decodable, Identifiable {
    let id: Int
    let at: Date
    let momentId: String
    let offsetSeconds: Double
    let image: String?

    var fact: Fact {
        Fact(id: -id, kind: "frame", at: at, momentId: momentId, offsetSeconds: offsetSeconds, text: "", image: image)
    }
}

struct PersonRecording: Decodable, Identifiable {
    let id: String
    let title: String?
    let startedAt: Date
    let durationSeconds: Double

    var fact: Fact {
        Fact(id: -abs(id.hashValue), kind: "recording", at: startedAt, momentId: id, offsetSeconds: 0, text: title ?? "", image: nil)
    }
}

struct PersonView: View {
    let person: Person
    let others: [Person]

    @Environment(Router.self) private var router
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var savedName: String?
    @State private var status: String?
    @State private var detail: PersonDetail?
    @State private var merging = false
    @State private var confirmingForget = false
    @State private var asking = false
    @State private var working = false
    @FocusState private var editingName: Bool

    var body: some View {
        ScrollView {
            VStack(spacing: 28) {
                header

                if let photos = detail?.photos, !photos.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(Array(photos.enumerated()), id: \.element.id) { index, photo in
                                Button {
                                    router.openSearch(photos.map(\.fact), at: index, query: "")
                                } label: {
                                    FrameImage(path: photo.image, small: true)
                                        .frame(width: 128, height: 96)
                                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                                        .overlay(alignment: .bottomLeading) {
                                            Text(whenLast(photo.at))
                                                .font(.caption2.weight(.semibold))
                                                .padding(.horizontal, 7)
                                                .padding(.vertical, 3)
                                                .background(.ultraThinMaterial, in: Capsule())
                                                .padding(6)
                                        }
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal)
                    }
                    .scrollClipDisabled()
                }

                if let recordings = detail?.recordings, !recordings.isEmpty {
                    VStack(alignment: .leading, spacing: 0) {
                        Text("Recordings")
                            .font(.headline)
                            .padding(.bottom, 6)

                        ForEach(recordings) { recording in
                            Button { router.open(recording.fact) } label: {
                                HStack {
                                    Text(recording.title ?? "Recording")
                                        .lineLimit(1)

                                    Spacer()

                                    Text(whenLast(recording.startedAt))
                                        .foregroundStyle(.secondary)
                                }
                                .padding(.vertical, 12)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)

                            if recording.id != recordings.last?.id {
                                Divider()
                            }
                        }
                    }
                    .padding(.horizontal)
                }

                Button { asking = true } label: {
                    Label(isMe ? "Ask about me" : "Ask about \(shownName)", systemImage: "bubble.left")
                        .font(.subheadline.weight(.medium))
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .overlay(Capsule().stroke(Color(.separator)))
                }
                .buttonStyle(.plain)
            }
            .padding(.vertical)
        }
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    if !others.isEmpty {
                        Button { merging = true } label: { Label("Merge with…", systemImage: "person.2") }
                    }

                    Button(role: .destructive) { confirmingForget = true } label: {
                        Label("Forget this person", systemImage: "trash")
                    }
                } label: {
                    Image(systemName: "ellipsis")
                }
                .disabled(working)
                .accessibilityLabel("More")
            }
        }
        .onAppear { if savedName == nil { name = person.name ?? ""; savedName = name } }
        .task { await load() }
        .onChange(of: editingName) { _, editing in if !editing { Task { await save() } } }
        .sheet(isPresented: $merging) {
            MergePicker(person: person, others: others) { other in Task { await merge(into: other) } }
        }
        .sheet(isPresented: $asking) {
            PersonChat(question: isMe ? "What have I been doing lately?" : "What do I know about \(shownName)?")
        }
        .confirmationDialog("Forget \(shownName)?", isPresented: $confirmingForget, titleVisibility: .visible) {
            Button("Forget", role: .destructive) { Task { await forget() } }
        } message: {
            Text("Their photos stay. This can't be undone.")
        }
    }

    private var isMe: Bool {
        name.trimmingCharacters(in: .whitespaces).lowercased() == "me"
    }

    private var shownName: String {
        let trimmed = name.trimmingCharacters(in: .whitespaces)

        return trimmed.isEmpty ? "this person" : trimmed
    }

    private var header: some View {
        VStack(spacing: 10) {
            FaceImage(path: person.face, size: 132)

            TextField("Add a name", text: $name)
                .font(.title2.weight(.semibold))
                .multilineTextAlignment(.center)
                .textInputAutocapitalization(.words)
                .submitLabel(.done)
                .focused($editingName)
                .padding(.horizontal)

            Text(status ?? "\(seenCount(person.faces)) · last \(whenLast(person.lastSeen))")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private func load() async {
        do {
            detail = try await API.current.get("/people/\(person.id)")
        } catch {
            status = error.localizedDescription
        }
    }

    private func save() async {
        let trimmed = name.trimmingCharacters(in: .whitespaces)

        guard trimmed != savedName else { return }

        do {
            let _: EmptyReply = try await API.current.send("/people/\(person.id)", method: "PUT",
                                                   body: NameUpdate(name: trimmed.isEmpty ? nil : trimmed))
            savedName = trimmed
            status = "Saved"
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

struct MergePicker: View {
    let person: Person
    let others: [Person]
    let merge: (Person) -> Void

    @Environment(\.dismiss) private var dismiss

    @State private var chosen: Person?

    var body: some View {
        NavigationStack {
            Group {
                if let chosen {
                    VStack(spacing: 24) {
                        HStack(spacing: 20) {
                            side(person)
                            Image(systemName: "equal")
                                .font(.title2)
                                .foregroundStyle(.secondary)
                            side(chosen)
                        }

                        Text("Same person?")
                            .font(.headline)

                        Button("Merge") {
                            merge(chosen)
                            dismiss()
                        }
                        .buttonStyle(FilledButton())
                    }
                    .padding()
                } else {
                    List(others) { other in
                        Button { chosen = other } label: {
                            HStack(spacing: 12) {
                                FaceImage(path: other.face, size: 40)
                                Text(other.label)
                            }
                        }
                        .buttonStyle(.plain)
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle(chosen == nil ? "Merge with" : "")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if chosen == nil {
                        Button("Cancel") { dismiss() }
                    } else {
                        Button { chosen = nil } label: { Image(systemName: "chevron.left") }
                            .accessibilityLabel("Back")
                    }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func side(_ someone: Person) -> some View {
        VStack(spacing: 8) {
            FaceImage(path: someone.face, size: 96)

            Text(someone.label)
                .font(.subheadline)
                .lineLimit(1)
        }
        .frame(width: 120)
    }
}

struct PersonChat: View {
    let question: String

    @Environment(ConversationStore.self) private var store
    @Environment(Router.self) private var router
    @Environment(\.dismiss) private var dismiss

    @State private var chat: UUID?
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            Group {
                if let chat {
                    ChatView(id: chat, newChat: { Task { await begin() } })
                } else if let problem {
                    ServerProblem(message: problem, retry: begin)
                } else {
                    ProgressView()
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .environment(\.openSource) { [router] in router.open($0) }
        .onChange(of: router.focusChangeCount) { dismiss() }
        .task { if chat == nil { await begin() } }
    }

    private func begin() async {
        guard let started = await store.start() else {
            problem = store.problem ?? "Couldn't start a chat."
            return
        }

        chat = started
        problem = nil
        Task { await store.ask(question, in: started) }
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
