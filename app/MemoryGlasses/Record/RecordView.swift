import ARKit
import SceneKit
import SwiftUI

struct CameraPreview: UIViewRepresentable {
    let session: ARSession

    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView()
        view.session = session
        view.automaticallyUpdatesLighting = false

        return view
    }

    func updateUIView(_ view: ARSCNView, context: Context) {}
}

struct RecordView: View {
    @Environment(\.dismiss) private var dismiss

    @StateObject private var recorder = Recorder()
    @State private var framesPerMinute = 30.0

    var body: some View {
        ZStack {
            CameraPreview(session: recorder.session)
                .ignoresSafeArea()

            VStack {
                topBar
                Spacer()
                bottomBar
            }
            .padding(20)
        }
        .background(.black)
        .onAppear { recorder.startCamera() }
        .task {
            if let saved: GlassesSettings = try? await API.current.get("/settings") {
                framesPerMinute = saved.framesPerMinute
            }
        }
        .onDisappear { recorder.stopCamera() }
    }

    private var topBar: some View {
        HStack(alignment: .top) {
            Button { dismiss() } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .semibold))
                    .frame(width: 40, height: 40)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .disabled(recorder.stage == .recording || recorder.stage == .finishing)

            Spacer()

            VStack(alignment: .trailing, spacing: 6) {
                if recorder.stage == .recording {
                    Text(clock(recorder.elapsed))
                        .font(.headline.monospacedDigit())
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(.red, in: Capsule())
                        .foregroundStyle(.white)
                }

                pill(recorder.tracking, systemImage: "location.viewfinder")

                if !recorder.room.isEmpty {
                    pill(recorder.room, systemImage: "house")
                }

                if recorder.hasLidar {
                    pill("Lidar", systemImage: "cube.transparent")
                }
            }
        }
        .foregroundStyle(.primary)
    }

    @ViewBuilder
    private var bottomBar: some View {
        switch recorder.stage {
        case .ready, .recording:
            VStack(spacing: 18) {
                if recorder.stage == .ready {
                    pill("\(Int(framesPerMinute)) frames a minute, from Glasses settings", systemImage: "photo.stack")
                }

                recordButton
            }
        case .finishing:
            status("Saving the recording…", progress: nil)
        case .uploading(let fraction):
            status("Uploading \(Int(fraction * 100))%", progress: fraction)
        case .uploaded:
            VStack(spacing: 12) {
                status("Uploaded. It's being processed and will appear on the Timeline.", progress: nil)
                Button("Done") { dismiss() }
                    .buttonStyle(FilledButton())
            }
        case .failed(let message):
            VStack(spacing: 12) {
                status(message, progress: nil)
                Button("Close") { dismiss() }
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
            }
        }
    }

    private var recordButton: some View {
        Button {
            if recorder.stage == .recording {
                recorder.stop(framesPerMinute: framesPerMinute)
            } else {
                recorder.start()
            }
        } label: {
            ZStack {
                Circle()
                    .stroke(.white, lineWidth: 5)
                    .frame(width: 78, height: 78)

                RoundedRectangle(cornerRadius: recorder.stage == .recording ? 8 : 32, style: .continuous)
                    .fill(.red)
                    .frame(width: recorder.stage == .recording ? 32 : 64, height: recorder.stage == .recording ? 32 : 64)
                    .animation(.snappy, value: recorder.stage)
            }
        }
        .disabled(!recorder.canRecord)
        .opacity(recorder.canRecord ? 1 : 0.4)
        .accessibilityLabel(recorder.stage == .recording ? "Stop recording" : "Start recording")
    }

    private func pill(_ text: String, systemImage: String) -> some View {
        Label(text, systemImage: systemImage)
            .font(.caption.weight(.medium))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(.ultraThinMaterial, in: Capsule())
    }

    private func status(_ text: String, progress: Double?) -> some View {
        VStack(spacing: 10) {
            Text(text)
                .font(.subheadline.weight(.medium))
                .multilineTextAlignment(.center)

            if let progress {
                ProgressView(value: progress)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
    }
}
