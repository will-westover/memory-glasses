import SwiftUI

enum GlassesActivity {
    case updating
    case recording
    case waiting
    case paused
    case disconnected

    var title: String {
        switch self {
        case .updating: "Updating"
        case .recording: "Recording"
        case .waiting: "Ready"
        case .paused: "Paused"
        case .disconnected: "Not connected"
        }
    }

    var symbol: String {
        switch self {
        case .updating: "arrow.down.circle.fill"
        case .recording: "record.circle.fill"
        case .waiting: "eyeglasses"
        case .paused: "eyeglasses"
        case .disconnected: "eyeglasses.slash"
        }
    }

    var tint: Color {
        switch self {
        case .updating: .blue
        case .recording: .red
        case .waiting: .green
        case .paused, .disconnected: .secondary
        }
    }
}

struct GlassesStatusCard: View {
    let activity: GlassesActivity
    let detail: String
    let photos: Int
    let lastUpload: Date?
    let canRecord: Bool
    @Binding var recording: Bool

    private let link = GlassesLink.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                Image(systemName: activity.symbol)
                    .font(.system(size: 18))
                    .foregroundStyle(activity.tint)
                    .frame(width: 28)
                    .contentTransition(.symbolEffect(.replace))

                VStack(alignment: .leading, spacing: 2) {
                    Text(activity.title)
                        .font(.headline)

                    Text(detail)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 8)

                Toggle("Recording", isOn: $recording)
                    .labelsHidden()
                    .disabled(!canRecord)
            }

            HStack(alignment: .top) {
                figure("Photos", "\(photos)")
                figure("Last upload", lastUpload?.formatted(date: .omitted, time: .shortened) ?? "–")
                figure("Firmware", link.glassesVersion.map(Self.shortVersion) ?? "–")
            }

            firmware
        }
        .padding(.vertical, 6)
    }

    @ViewBuilder
    private var firmware: some View {
        if let progress = link.updateProgress, progress < 1 {
            VStack(alignment: .leading, spacing: 8) {
                ProgressView(value: progress) {
                    Text(link.updateMessage ?? "Updating…")
                        .font(.subheadline)
                } currentValueLabel: {
                    Text("\(Int(progress * 100))%")
                }

                Button("Cancel update", role: .destructive) { link.cancelUpdate() }
                    .font(.subheadline)
            }
        } else if let update = link.availableUpdate {
            Button("Update glasses to \(Self.shortVersion(update.version))") { Task { await link.startUpdate() } }
                .buttonStyle(.bordered)
                .disabled(!link.canUpdate)
        } else if link.glassesVersion != nil {
            HStack {
                Text(link.updateMessage ?? "Up to date")
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                Spacer()

                if link.isCheckingForUpdate {
                    ProgressView().controlSize(.mini)
                } else {
                    Button("Check for updates") { Task { await link.checkForUpdate() } }
                        .font(.footnote)
                }
            }
        }
    }

    private func figure(_ name: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value)
                .font(.subheadline.weight(.semibold))
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.7)

            Text(name)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    static func shortVersion(_ version: String) -> String {
        let parts = version.split(separator: "-")

        guard parts.count >= 2, parts[0].count == 8, parts[1].count == 4 else { return version }

        let day = parts[0], time = parts[1]

        return "\(day.dropFirst(4).prefix(2))/\(day.suffix(2)) \(time.prefix(2)):\(time.suffix(2))"
    }
}

struct GlassesDisplayPreview: View {
    let settings: GlassesSettings
    let lines: [String]

    var body: some View {
        GeometryReader { space in
            let width = space.size.width * settings.displayScale
            let height = space.size.height * settings.displayScale
            let x = (space.size.width - width) / 2 * (1 + settings.displayX)
            let y = (space.size.height - height) / 2 * (1 + settings.displayY)

            VStack(alignment: .leading, spacing: 1) {
                ForEach(lines.filter { !$0.isEmpty }, id: \.self) { line in
                    Text(line)
                        .font(.system(size: settings.displayLargeText ? 15 : 9, weight: .medium, design: .monospaced))
                        .lineLimit(1)
                        .minimumScaleFactor(0.5)
                }
            }
            .foregroundStyle(.white.opacity(0.35 + 0.65 * settings.displayBrightness))
            .frame(width: width, height: height, alignment: .topLeading)
            .overlay {
                if settings.displayScale < 1 || settings.displayPattern {
                    Rectangle().stroke(.white.opacity(0.4), lineWidth: 0.5)
                }
            }
            .offset(x: x, y: y)
        }
        .aspectRatio(2, contentMode: .fit)
        .padding(4)
        .background(.black, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
        .accessibilityLabel("What the glasses show: \(lines.joined(separator: ", "))")
    }
}
