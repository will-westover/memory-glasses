import SwiftUI

struct InfoButton: View {
    let title: String
    let text: String

    @State private var shown = false

    var body: some View {
        Button { shown = true } label: {
            Image(systemName: "questionmark.circle")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("About \(title)")
        .popover(isPresented: $shown, attachmentAnchor: .rect(.bounds), arrowEdge: .top) {
            VStack(alignment: .leading, spacing: 8) {
                Text(title)
                    .font(.headline)
                    .foregroundStyle(Color.primary)

                Text(text)
                    .font(.subheadline)
                    .foregroundStyle(Color.primary.opacity(0.75))
                    .fixedSize(horizontal: false, vertical: true)
            }
            .textCase(nil)
            .frame(width: 250, alignment: .leading)
            .padding(.horizontal, 20)
            .padding(.vertical, 18)
            .presentationCompactAdaptation(.popover)
        }
    }
}

struct InfoHeader: View {
    let title: String
    let text: String

    var body: some View {
        HStack(spacing: 6) {
            Text(title)

            InfoButton(title: title, text: text)
        }
        .textCase(nil)
    }
}
