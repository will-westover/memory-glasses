import CoreLocation

struct TrailPoint: Encodable {
    let at: String
    let lat: Double
    let lon: Double
    let accuracyM: Double
}

struct Trail: Encodable {
    let points: [TrailPoint]
}

@MainActor
final class LocationTrail: NSObject, CLLocationManagerDelegate {
    static let shared = LocationTrail()
    static let stillHereSeconds = 60.0

    private let manager = CLLocationManager()
    private var waiting: [TrailPoint] = []
    private var latest: CLLocation?
    private var lastPointAt = Date.distantPast
    private var sending = false
    private var started = false

    func start() {
        guard !started else { return }

        started = true
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        manager.distanceFilter = 25
        manager.pausesLocationUpdatesAutomatically = false
        manager.requestWhenInUseAuthorization()

        if manager.authorizationStatus == .authorizedWhenInUse || manager.authorizationStatus == .authorizedAlways {
            manager.allowsBackgroundLocationUpdates = true
        }

        manager.startUpdatingLocation()
    }

    func send() async {
        if let latest, Date().timeIntervalSince(lastPointAt) >= Self.stillHereSeconds {
            keep(latest, at: Date())
        }

        guard !sending, !waiting.isEmpty else { return }

        sending = true
        defer { sending = false }

        let batch = waiting

        if (try? await API.current.send("/locations", method: "POST", body: Trail(points: batch)) as EmptyReply) != nil {
            waiting.removeFirst(min(batch.count, waiting.count))
        }
    }

    private func keep(_ location: CLLocation, at: Date) {
        waiting.append(TrailPoint(at: at.ISO8601Format(), lat: location.coordinate.latitude, lon: location.coordinate.longitude,
                                  accuracyM: location.horizontalAccuracy))
        lastPointAt = at
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        Task { @MainActor in
            for location in locations where location.horizontalAccuracy >= 0 {
                self.latest = location

                if GlassesLink.shared.state == .connected {
                    self.keep(location, at: location.timestamp)
                }
            }
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor in
            if manager.authorizationStatus == .authorizedWhenInUse || manager.authorizationStatus == .authorizedAlways {
                manager.allowsBackgroundLocationUpdates = true
            }
        }
    }
}
