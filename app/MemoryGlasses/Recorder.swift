import ARKit
import AVFoundation
import CoreLocation

final class Recorder: NSObject, ObservableObject, ARSessionDelegate, CLLocationManagerDelegate {
    enum Stage: Equatable {
        case ready
        case recording
        case finishing
        case uploading(Double)
        case uploaded
        case failed(String)
    }

    static let videoWidth = 1440
    static let videoHeight = 1080
    static let videoBitrate = 6_000_000

    @Published private(set) var stage: Stage = .ready
    @Published private(set) var elapsed: TimeInterval = 0
    @Published private(set) var tracking = "Starting camera…"

    let session = ARSession()
    let hasLidar = ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification)
    let canRecord = ARWorldTrackingConfiguration.isSupported

    private let queue = DispatchQueue(label: "recorder")
    private let locationManager = CLLocationManager()
    private var location: CLLocation?

    private var writer: AVAssetWriter?
    private var videoInput: AVAssetWriterInput?
    private var audioInput: AVAssetWriterInput?
    private var adaptor: AVAssetWriterInputPixelBufferAdaptor?
    private var firstTime: TimeInterval?
    private var lastWritten: TimeInterval = 0
    private var isRecording = false
    private var startedAt = Date()
    private var outputURL: URL?

    override init() {
        super.init()
        session.delegate = self
        session.delegateQueue = queue
        locationManager.delegate = self
    }

    func startCamera() {
        guard canRecord else {
            tracking = "Recording needs a real iPhone"
            return
        }

        let configuration = ARWorldTrackingConfiguration()
        configuration.providesAudioData = true

        session.run(configuration, options: [.resetTracking, .removeExistingAnchors])
        locationManager.requestWhenInUseAuthorization()
        locationManager.startUpdatingLocation()
    }

    func stopCamera() {
        session.pause()
        locationManager.stopUpdatingLocation()
    }

    func start() {
        let url = FileManager.default.temporaryDirectory.appending(path: "recording-\(UUID().uuidString).mov")

        do {
            try makeWriter(at: url)
        } catch {
            stage = .failed("Couldn't start recording: \(error.localizedDescription)")
            return
        }

        outputURL = url
        startedAt = .now
        elapsed = 0

        queue.async {
            self.firstTime = nil
            self.lastWritten = 0
            self.isRecording = true
        }

        stage = .recording
    }

    func stop(framesPerMinute: Double) {
        stage = .finishing

        queue.async {
            self.isRecording = false

            guard let writer = self.writer, writer.status == .writing else {
                DispatchQueue.main.async { self.stage = .failed("Nothing was recorded.") }
                return
            }

            self.videoInput?.markAsFinished()
            self.audioInput?.markAsFinished()

            writer.finishWriting {
                DispatchQueue.main.async { Task { await self.upload(framesPerMinute: framesPerMinute) } }
            }
        }
    }

    private func makeWriter(at url: URL) throws {
        let writer = try AVAssetWriter(outputURL: url, fileType: .mov)

        let video = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: Self.videoWidth,
            AVVideoHeightKey: Self.videoHeight,
            AVVideoScalingModeKey: AVVideoScalingModeResizeAspectFill,
            AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: Self.videoBitrate],
        ])
        video.expectsMediaDataInRealTime = true
        video.transform = CGAffineTransform(rotationAngle: .pi / 2)

        let audio = AVAssetWriterInput(mediaType: .audio, outputSettings: [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVNumberOfChannelsKey: 1,
            AVSampleRateKey: 44_100,
            AVEncoderBitRateKey: 64_000,
        ])
        audio.expectsMediaDataInRealTime = true

        writer.add(video)
        writer.add(audio)
        writer.metadata = metadata()

        self.writer = writer
        videoInput = video
        audioInput = audio
        adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: video, sourcePixelBufferAttributes: nil)
    }

    private func metadata() -> [AVMetadataItem] {
        let date = AVMutableMetadataItem()
        date.identifier = .quickTimeMetadataCreationDate
        date.value = ISO8601DateFormatter().string(from: .now) as NSString

        var items: [AVMetadataItem] = [date]

        if let location {
            let place = AVMutableMetadataItem()
            place.identifier = .quickTimeMetadataLocationISO6709
            place.value = String(format: "%+.6f%+.6f%+.3f/", location.coordinate.latitude,
                                 location.coordinate.longitude, location.altitude) as NSString
            items.append(place)
        }

        return items
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let state = describe(frame.camera.trackingState)
        DispatchQueue.main.async { if self.tracking != state { self.tracking = state } }

        guard isRecording, let writer, let videoInput, let adaptor else { return }

        if firstTime == nil {
            firstTime = frame.timestamp
            writer.startWriting()
            writer.startSession(atSourceTime: CMTime(seconds: frame.timestamp, preferredTimescale: 600))
        }

        guard frame.timestamp - lastWritten >= 1.0 / 30, videoInput.isReadyForMoreMediaData else { return }

        adaptor.append(frame.capturedImage, withPresentationTime: CMTime(seconds: frame.timestamp, preferredTimescale: 600))
        lastWritten = frame.timestamp

        let seconds = frame.timestamp - (firstTime ?? frame.timestamp)
        DispatchQueue.main.async { self.elapsed = seconds }
    }

    func session(_ session: ARSession, didOutputAudioSampleBuffer audioSampleBuffer: CMSampleBuffer) {
        guard isRecording, firstTime != nil, let audioInput, audioInput.isReadyForMoreMediaData else { return }

        audioInput.append(audioSampleBuffer)
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        DispatchQueue.main.async { self.tracking = "Camera error: \(error.localizedDescription)" }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        location = locations.last
    }

    private func describe(_ state: ARCamera.TrackingState) -> String {
        switch state {
        case .normal: "Tracking"
        case .notAvailable: "Tracking unavailable"
        case .limited(.initializing): "Starting tracking…"
        case .limited(.excessiveMotion): "Moving too fast"
        case .limited(.insufficientFeatures): "Not enough detail to track"
        case .limited(.relocalizing): "Recognising the room…"
        case .limited: "Tracking limited"
        }
    }

    private func upload(framesPerMinute: Double) async {
        guard let outputURL else { return }

        stage = .uploading(0)

        do {
            try await API.current.uploadMoment(video: outputURL, framesPerMinute: framesPerMinute) { progress in
                DispatchQueue.main.async { self.stage = .uploading(progress) }
            }

            try? FileManager.default.removeItem(at: outputURL)
            stage = .uploaded
        } catch {
            stage = .failed("Upload failed: \(error.localizedDescription). The recording is still on the phone.")
        }
    }
}
