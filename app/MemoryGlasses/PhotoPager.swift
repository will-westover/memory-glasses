import SwiftUI

struct PhotoPager: View {
    let frames: [Frame]
    @Binding var index: Int

    @State private var asking: FrameContext?

    var body: some View {
        VStack(spacing: 14) {
            TabView(selection: $index) {
                ForEach(frames.indices, id: \.self) { position in
                    FrameImage(path: frames[position].image)
                        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                        .contentShape(.contextMenuPreview, RoundedRectangle(cornerRadius: 18, style: .continuous))
                        .contextMenu {
                            Button { asking = FrameContext(frames[position]) } label: {
                                Label("Ask about this", systemImage: "bubble.left.and.text.bubble.right")
                            }
                        }
                        .padding(.horizontal, 20)
                        .tag(position)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .aspectRatio(4 / 3, contentMode: .fit)

            HStack {
                step("chevron.left", by: -1, label: "Previous photo")

                Spacer()

                Text("\(index + 1) of \(frames.count)")
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(.secondary)

                Spacer()

                step("chevron.right", by: 1, label: "Next photo")
            }
            .padding(.horizontal, 20)

            Filmstrip(frames: frames, index: $index)
        }
        .sheet(item: $asking) { FrameChat(context: $0) }
    }

    private func step(_ symbol: String, by change: Int, label: String) -> some View {
        let target = index + change
        let possible = frames.indices.contains(target)

        return Button {
            withAnimation(.snappy) { index = target }
        } label: {
            Image(systemName: symbol)
                .font(.system(size: 15, weight: .semibold))
                .frame(width: 40, height: 40)
                .background(Color(.secondarySystemBackground), in: Circle())
        }
        .disabled(!possible)
        .opacity(possible ? 1 : 0.3)
        .accessibilityLabel(label)
    }
}

struct Filmstrip: View {
    let frames: [Frame]
    @Binding var index: Int

    var body: some View {
        ScrollViewReader { strip in
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 6) {
                    ForEach(frames.indices, id: \.self) { position in
                        FrameImage(path: frames[position].image)
                            .frame(width: 56, height: 42)
                            .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                            .overlay {
                                RoundedRectangle(cornerRadius: 7, style: .continuous)
                                    .stroke(Color.primary, lineWidth: position == index ? 2.5 : 0)
                            }
                            .opacity(position == index ? 1 : 0.55)
                            .onTapGesture { withAnimation(.snappy) { index = position } }
                            .id(position)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 2)
            }
            .frame(height: 46)
            .onAppear { strip.scrollTo(index, anchor: .center) }
            .onChange(of: index) { withAnimation(.snappy) { strip.scrollTo(index, anchor: .center) } }
        }
    }
}

extension FrameContext: Identifiable {
    init(_ frame: Frame) {
        self.init(id: frame.id, image: frame.image, at: frame.at, summary: frame.summary)
    }
}

struct FrameChat: View {
    let context: FrameContext

    @Environment(ConversationStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var path = NavigationPath()
    @State private var chat: UUID?
    @State private var problem: String?

    var body: some View {
        NavigationStack(path: $path) {
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
            .navigationDestination(for: Fact.self) { FrameView(fact: $0) }
            .navigationDestination(for: MomentLink.self) { MomentView(link: $0) }
        }
        .environment(\.openSource) { path.append($0) }
        .task { if chat == nil { await begin() } }
    }

    private func begin() async {
        if let started = await store.start(about: context) {
            chat = started
            problem = nil
        } else {
            problem = store.problem ?? "Couldn't start a chat."
        }
    }
}
