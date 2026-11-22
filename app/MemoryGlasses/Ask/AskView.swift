import SwiftUI

struct AskView: View {
    @Environment(ConversationStore.self) private var store
    @Environment(Router.self) private var router

    @State private var path = NavigationPath()
    @State private var search = ""

    var body: some View {
        NavigationStack(path: $path) {
            let groups = store.groups()

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
                .task(id: search) {
                    if !search.isEmpty { try? await Task.sleep(for: .milliseconds(300)) }

                    guard !Task.isCancelled else { return }

                    await store.search(search)
                }
        }
        .environment(\.openSource) { [router] in router.open($0) }
    }

    @ViewBuilder
    private func chats(_ groups: [ConversationGroup]) -> some View {
        if !store.loaded {
            PlaceholderRows(count: 8, image: false)
        } else if groups.isEmpty {
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
                    .buttonStyle(FilledButton())
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
            Text(conversation.label)
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
