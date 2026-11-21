import SwiftUI

struct Composer: View {
    @Binding var text: String
    let busy: Bool
    var focused: FocusState<Bool>.Binding
    let send: () -> Void

    @State private var dictation = Dictation()

    private var ready: Bool { !busy && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        HStack(alignment: .bottom, spacing: 4) {
            TextField(dictation.listening ? "Listening…" : "Ask anything", text: $text, axis: .vertical)
                .lineLimit(1...5)
                .focused(focused)
                .onSubmit { if ready { send() } }
                .padding(.leading, 18)
                .padding(.vertical, 13)

            if text.isEmpty || dictation.listening {
                microphone
            }

            Button(action: send) {
                Image(systemName: "arrow.up")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Color(.systemBackground))
                    .frame(width: 32, height: 32)
                    .background(ready ? Color.primary : Color(.systemGray4), in: Circle())
            }
            .disabled(!ready)
            .padding(7)
            .accessibilityLabel("Send")
        }
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 24, style: .continuous))
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(.bar)
        .alert("Can't listen", isPresented: Binding(get: { dictation.problem != nil }, set: { if !$0 { dictation.problem = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(dictation.problem ?? "")
        }
    }

    private var microphone: some View {
        Image(systemName: dictation.listening ? "waveform" : "mic")
            .font(.system(size: 17, weight: .medium))
            .symbolEffect(.variableColor.iterative, isActive: dictation.listening)
            .foregroundStyle(dictation.listening ? Color.red : Color.secondary)
            .frame(width: 32, height: 32)
            .padding(.vertical, 7)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        if !dictation.listening && !busy {
                            Task { await dictation.start { text = $0 } }
                        }
                    }
                    .onEnded { _ in
                        dictation.stop()

                        if ready { send() }
                    }
            )
            .accessibilityLabel("Hold to talk")
    }
}
