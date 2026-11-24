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
    static let poseInterval = 0.1
    static let recogniseSeconds = 10.0
    static let locationWaitSteps = 15

    @Published private(set) var stage: Stage = .ready
    @Published private(set) var elapsed: TimeInterval = 0
    @Published private(set) var trackingMessage = "Starting camera…"
    @Published private(set) var placeMessage = ""

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
    private var firstFrameTime: TimeInterval?
    private var lastVideoFrameTime: TimeInterval = 0
    private var isRecording = false
    private var poses: [[Double]] = []
    private var lastPoseTime: TimeInterval = 0
    private var startedAt = Date()
    private var outputURL: URL?
    private var candidatePlace: Place?
    private var recognisedPlace: Place?
    private var lookingSince = Date()

    override init() {
        super.init()
        session.delegate = self
        session.delegateQueue = queue
        locationManager.delegate = self
    }

    func startCamera() {
        guard canRecord else {
            trackingMessage = "Recording needs a real iPhone"
            return
        }

        locationManager.requestWhenInUseAuthorization()
        locationManager.startUpdatingLocation()

        Task { await startTracking() }
    }

    @MainActor
    private func startTracking() async {
        candidatePlace = nil
        recognisedPlace = nil
        placeMessage = "Looking for a known place…"

        let configuration = freshConfiguration()

        if let (known, map) = await nearestPlaceMap() {
            candidatePlace = known
            configuration.initialWorldMap = map
        } else {
            placeMessage = "New place"
        }

        lookingSince = .now
        session.run(configuration, options: [.resetTracking, .removeExistingAnchors])
    }

    private func freshConfiguration() -> ARWorldTrackingConfiguration {
        let configuration = ARWorldTrackingConfiguration()
        configuration.providesAudioData = true

        if hasLidar {
            configuration.sceneReconstruction = .meshWithClassification
        }

        return configuration
    }

    private func nearestPlaceMap() async -> (Place, ARWorldMap)? {
        var path = "/places"

        for _ in 0..<Self.locationWaitSteps where location == nil && locationManager.location == nil {
            try? await Task.sleep(for: .milliseconds(200))
        }

        if let location = location ?? locationManager.location {
            path += "?lat=\(location.coordinate.latitude)&lon=\(location.coordinate.longitude)"
        }

        guard let places: [Place] = try? await API.current.get(path), let latest = places.first(where: \.hasMap),
              let data = try? await API.current.download("/places/\(latest.id)/map"),
              let map = try? NSKeyedUnarchiver.unarchivedObject(ofClass: ARWorldMap.self, from: data)
        else { return nil }

        return (latest, map)
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
            if self.candidatePlace != nil, self.recognisedPlace == nil {
                self.giveUpLooking()
            }

            self.firstFrameTime = nil
            self.lastVideoFrameTime = 0
            self.poses = []
            self.lastPoseTime = 0
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

            let posesFile = self.writePoses()

            let meshFile = self.hasLidar ? self.writeMesh() : nil

            writer.finishWriting {
                self.session.getCurrentWorldMap { map, _ in
                    let mapFile = map.flatMap { self.writeWorldMap($0) }
                    var extras: [(field: String, url: URL)] = []

                    if let posesFile { extras.append(("poses", posesFile)) }
                    if let meshFile { extras.append(("mesh", meshFile)) }
                    if let mapFile { extras.append(("world_map", mapFile)) }

                    DispatchQueue.main.async {
                        Task { await self.upload(framesPerMinute: framesPerMinute, extras: extras) }
                    }
                }
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
        DispatchQueue.main.async { if self.trackingMessage != state { self.trackingMessage = state } }
        checkPlaceRecognised(frame)

        guard isRecording, let writer, let videoInput, let adaptor else { return }

        if firstFrameTime == nil {
            firstFrameTime = frame.timestamp
            writer.startWriting()
            writer.startSession(atSourceTime: CMTime(seconds: frame.timestamp, preferredTimescale: 600))
        }

        guard frame.timestamp - lastVideoFrameTime >= 1.0 / 30, videoInput.isReadyForMoreMediaData else { return }

        adaptor.append(frame.capturedImage, withPresentationTime: CMTime(seconds: frame.timestamp, preferredTimescale: 600))
        lastVideoFrameTime = frame.timestamp
        logPose(frame)

        let seconds = frame.timestamp - (firstFrameTime ?? frame.timestamp)
        DispatchQueue.main.async { self.elapsed = seconds }
    }

    private func checkPlaceRecognised(_ frame: ARFrame) {
        guard let candidatePlace, recognisedPlace == nil else { return }

        if case .normal = frame.camera.trackingState {
            recognisedPlace = candidatePlace
            DispatchQueue.main.async { self.placeMessage = "Recognised \(candidatePlace.label)" }
        } else if Date.now.timeIntervalSince(lookingSince) > Self.recogniseSeconds {
            giveUpLooking()
        }
    }

    private func giveUpLooking() {
        candidatePlace = nil
        DispatchQueue.main.async { self.placeMessage = "New place" }
        session.run(freshConfiguration(), options: [.resetTracking, .removeExistingAnchors])
    }

    private func logPose(_ frame: ARFrame) {
        guard frame.timestamp - lastPoseTime >= Self.poseInterval, case .normal = frame.camera.trackingState else { return }

        let transform = frame.camera.transform
        let forward = -transform.columns.2
        let seconds = frame.timestamp - (firstFrameTime ?? frame.timestamp)

        let columns = [transform.columns.0, transform.columns.1, transform.columns.2, transform.columns.3]
        let intrinsics = frame.camera.intrinsics
        let view = columns.flatMap { [Double($0.x), Double($0.y), Double($0.z), Double($0.w)] }
            + [Double(intrinsics.columns.0.x), Double(intrinsics.columns.1.y),
               Double(intrinsics.columns.2.x), Double(intrinsics.columns.2.y),
               Double(frame.camera.imageResolution.width), Double(frame.camera.imageResolution.height)]

        poses.append([seconds, Double(transform.columns.3.x), Double(transform.columns.3.y),
                      Double(transform.columns.3.z), Double(atan2(forward.x, forward.z))] + view)
        lastPoseTime = frame.timestamp
    }

    private func writePoses() -> URL? {
        guard !poses.isEmpty else { return nil }

        let url = FileManager.default.temporaryDirectory.appending(path: "poses-\(UUID().uuidString).json")
        let data = try? JSONSerialization.data(withJSONObject: ["poses": poses])

        return (try? data?.write(to: url)) != nil ? url : nil
    }

    private func writeMesh() -> URL? {
        let anchors = session.currentFrame?.anchors.compactMap { $0 as? ARMeshAnchor } ?? []

        guard !anchors.isEmpty else { return nil }

        let url = FileManager.default.temporaryDirectory.appending(path: "mesh-\(UUID().uuidString).obj")
        FileManager.default.createFile(atPath: url.path, contents: nil)

        guard let out = try? FileHandle(forWritingTo: url) else { return nil }
        defer { try? out.close() }

        var vertexBase = 1

        for anchor in anchors {
            let geometry = anchor.geometry
            var text = ""

            for index in 0..<geometry.vertices.count {
                let local = geometry.vertices.value(at: index)
                let world = anchor.transform * SIMD4<Float>(local.x, local.y, local.z, 1)
                text += "v \(world.x) \(world.y) \(world.z)\n"
            }

            for index in 0..<geometry.faces.count {
                let face = geometry.faces.indices(of: index).map { $0 + vertexBase }
                text += "f \(face[0]) \(face[1]) \(face[2])\n"
            }

            out.write(Data(text.utf8))
            vertexBase += geometry.vertices.count
        }

        return url
    }

    private func writeWorldMap(_ map: ARWorldMap) -> URL? {
        guard let data = try? NSKeyedArchiver.archivedData(withRootObject: map, requiringSecureCoding: true) else {
            return nil
        }

        let url = FileManager.default.temporaryDirectory.appending(path: "world-\(UUID().uuidString).map")

        return (try? data.write(to: url)) != nil ? url : nil
    }

    func session(_ session: ARSession, didOutputAudioSampleBuffer audioSampleBuffer: CMSampleBuffer) {
        guard isRecording, firstFrameTime != nil, let audioInput, audioInput.isReadyForMoreMediaData else { return }

        audioInput.append(audioSampleBuffer)
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        DispatchQueue.main.async { self.trackingMessage = "Camera error: \(error.localizedDescription)" }
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
        case .limited(.relocalizing): "Recognising the place…"
        case .limited: "Tracking limited"
        }
    }

    private func upload(framesPerMinute: Double, extras: [(field: String, url: URL)]) async {
        guard let outputURL else { return }

        stage = .uploading(0)

        do {
            try await API.current.uploadMoment(video: outputURL, framesPerMinute: framesPerMinute, placeId: recognisedPlace?.id,
                                               extras: extras) { progress in
                DispatchQueue.main.async { self.stage = .uploading(progress) }
            }

            try? FileManager.default.removeItem(at: outputURL)
            extras.forEach { try? FileManager.default.removeItem(at: $0.url) }
            stage = .uploaded
        } catch {
            stage = .failed("Upload failed: \(error.localizedDescription). The recording is still on the phone.")
        }
    }
}

extension ARGeometrySource {
    func value(at index: Int) -> SIMD3<Float> {
        let pointer = buffer.contents().advanced(by: offset + stride * index)

        let (x, y, z) = pointer.assumingMemoryBound(to: (Float, Float, Float).self).pointee

        return SIMD3<Float>(x, y, z)
    }
}

extension ARGeometryElement {
    func indices(of face: Int) -> [Int] {
        let pointer = buffer.contents().advanced(by: face * indexCountPerPrimitive * bytesPerIndex)

        return (0..<indexCountPerPrimitive).map { corner in
            Int(pointer.advanced(by: corner * bytesPerIndex).assumingMemoryBound(to: UInt32.self).pointee)
        }
    }
}
