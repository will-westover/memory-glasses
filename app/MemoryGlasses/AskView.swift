import SwiftUI

struct AskView: View {
    @Environment(ConversationStore.self) private var store

    @State private var path = NavigationPath()
    @State private var search = ""

    var body: some View {
        NavigationStack(path: $path) {
            let groups = store.groups(matching: search)

            chats(groups)
                .searchable(text: $search, prompt: "Search chats")
                .navigationTitle("Ask")
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button(action: newChat) { Image(systemName: "square.and.pencil") }
                            .accessibilityLabel("New chat")
                    }
                }
                .navigationDestination(for: UUID.self) { id in
                    ChatView(id: id, newChat: newChat)
                }
                .navigationDestination(for: Fact.self) { MomentView(fact: $0) }
                .navigationDestination(for: SessionLink.self) { SessionView(link: $0) }
                .task { await store.refresh() }
        }
    }

    @ViewBuilder
    private func chats(_ groups: [ConversationGroup]) -> some View {
        if groups.isEmpty {
            emptyList
        } else {
            List {
                ForEach(groups) { group in
                    Section {
                        ForEach(group.conversations) { conversation in
                            NavigationLink(value: conversation.id) { ConversationRow(conversation: conversation) }
                        }
                        .onDelete { offsets in
                            let ids = offsets.map { group.conversations[$0].id }
                            Task { await store.delete(ids) }
                        }
                    } header: {
                        Text(group.title)
                            .font(.footnote.weight(.medium))
                            .foregroundStyle(.secondary)
                            .textCase(nil)
                    }
                }
            }
            .listStyle(.plain)
            .refreshable { await store.refresh() }
        }
    }

    @ViewBuilder
    private var emptyList: some View {
        if let problem = store.problem, search.isEmpty {
            ServerProblem(message: problem, retry: store.refresh)
        } else if search.isEmpty {
            ContentUnavailableView {
                Label("No chats yet", systemImage: "bubble.left.and.text.bubble.right")
            } description: {
                Text("Ask about anything you saw or heard.")
            } actions: {
                Button("New chat", action: newChat)
                    .buttonStyle(.borderedProminent)
                    .buttonBorderShape(.capsule)
            }
        } else {
            ContentUnavailableView.search(text: search)
        }
    }

    private func newChat() {
        Task {
            if let id = await store.start() {
                path = NavigationPath([id])
            }
        }
    }
}

struct ConversationRow: View {
    let conversation: ConversationSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(conversation.title)
                .font(.body.weight(.medium))
                .lineLimit(1)

            Text(conversation.preview)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .padding(.vertical, 4)
    }
}
