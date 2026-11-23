import MapKit
import SceneKit
import SwiftUI

struct WorldView: View {
    @State private var world: World?
    @State private var problem: String?
    @State private var camera: MapCameraPosition = .automatic
    @State private var selectedPlace: WorldPlace?
    @State private var selectedMoment: MomentRoute?

    var body: some View {
        NavigationStack {
            Group {
                if let world {
                    content(world)
                } else if let problem {
                    ServerProblem(message: problem, retry: load)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("World")
            .navigationDestination(for: WorldPlace.self) { PlaceSceneView(place: $0) }
            .navigationDestination(for: MomentRoute.self) { MomentView(link: $0) }
            .navigationDestination(for: Fact.self) { FrameView(fact: $0) }
            .navigationDestination(item: $selectedPlace) { PlaceSceneView(place: $0) }
            .navigationDestination(item: $selectedMoment) { MomentView(link: $0) }
            .task { await load() }
        }
    }

    @ViewBuilder
    private func content(_ world: World) -> some View {
        if world.moments.isEmpty {
            ContentUnavailableView("Nowhere yet", systemImage: "map",
                                   description: Text("Recordings with a location appear here."))
        } else {
            List {
                Section {
                    map(world)
                        .frame(height: 300)
                        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                        .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 8, trailing: 16))
                        .listRowSeparator(.hidden)
                }

                let places = world.places.filter { $0.moments > 0 }

                if !places.isEmpty {
                    Section {
                        ForEach(places) { place in
                            if place.hasMap {
                                NavigationLink(value: place) { placeRow(place) }
                            } else {
                                placeRow(place)
                            }
                        }
                    } header: {
                        SectionTitle("Places").textCase(nil)
                    }
                }

                Section {
                    NavigationLink { PeopleView() } label: {
                        Label("People", systemImage: "person.2")
                    }
                }

                Section {
                    ForEach(world.moments.filter { $0.place == nil }) { moment in
                        NavigationLink(value: MomentRoute(id: moment.id, startedAt: moment.startedAt)) {
                            Label(moment.startedAt.formatted(.dateTime.weekday(.abbreviated).month().day().hour().minute()),
                                  systemImage: sourceIcon(moment.source).symbol)
                        }
                    }
                } header: {
                    SectionTitle("Other recordings").textCase(nil)
                }
            }
            .listStyle(.plain)
            .refreshable { await load() }
        }
    }

    private func placeRow(_ place: WorldPlace) -> some View {
        let rooms = place.rooms.filter { $0.lowercased() != place.label.lowercased() }

        return VStack(alignment: .leading, spacing: 2) {
            Text(place.label).font(.body.weight(.medium))
            Text(rooms.isEmpty ? momentCount(place.moments) : rooms.joined(separator: ", "))
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
    }

    private func map(_ world: World) -> some View {
        Map(position: $camera) {
            ForEach(world.places) { place in
                Annotation(place.label, coordinate: CLLocationCoordinate2D(latitude: place.lat, longitude: place.lon)) {
                    Button { if place.hasMap { selectedPlace = place } } label: {
                        Image(systemName: "mappin")
                            .font(.caption)
                            .foregroundStyle(Color(.systemBackground))
                            .frame(width: 30, height: 30)
                            .background(Color.primary, in: Circle())
                    }
                }
            }

            ForEach(world.moments.filter { $0.place == nil }) { moment in
                Annotation("", coordinate: CLLocationCoordinate2D(latitude: moment.lat, longitude: moment.lon)) {
                    Button { selectedMoment = MomentRoute(id: moment.id, startedAt: moment.startedAt) } label: {
                        FrameImage(path: moment.cover, small: true)
                            .frame(width: 30, height: 30)
                            .clipShape(Circle())
                            .overlay(Circle().stroke(Color(.systemBackground), lineWidth: 2))
                    }
                }
            }
        }
        .mapStyle(.standard(pointsOfInterest: .excludingAll))
    }

    private func load() async {
        do {
            world = try await API.current.get("/world")
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }
}

struct PlaceSceneView: View {
    static let roomColors: [UIColor] = [.systemOrange, .systemBlue, .systemGreen, .systemPink, .systemPurple, .systemTeal,
                                    .systemYellow, .systemIndigo]

    let place: WorldPlace

    @State private var scene: SCNScene?
    @State private var rooms: [(name: String, frames: [(moment: SceneMoment, frame: Frame)])] = []
    @State private var problem: String?
    @State private var openedFrame: Fact?

    var body: some View {
        VStack(spacing: 0) {
            if let scene {
                RoomScene(scene: scene) { layer, index in open(layer, index) }

                legend
            } else if let problem {
                ContentUnavailableView("Couldn't load the place", systemImage: "cube.transparent", description: Text(problem))
            } else {
                ProgressView("Building the place…")
                    .frame(maxHeight: .infinity)
            }
        }
        .navigationTitle(place.label)
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(item: $openedFrame) { FrameView(fact: $0) }
        .task { await load() }
    }

    private var legend: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 14) {
                ForEach(Array(rooms.enumerated()), id: \.offset) { position, room in
                    HStack(spacing: 6) {
                        Circle()
                            .fill(Color(Self.roomColors[position % Self.roomColors.count]))
                            .frame(width: 10, height: 10)

                        Text(room.name)
                            .font(.caption)
                    }
                }
            }
            .padding(16)
        }
        .background(.bar)
    }

    private func open(_ layer: Int, _ index: Int) {
        guard rooms.indices.contains(layer), rooms[layer].frames.indices.contains(index) else { return }

        let (moment, frame) = rooms[layer].frames[index]

        openedFrame = Fact(id: -frame.id, kind: "frame", at: frame.at, momentId: moment.id, offsetSeconds: frame.offsetSeconds,
                      text: frame.summary, image: frame.image)
    }

    private func load() async {
        do {
            let loaded: PlaceScene = try await API.current.get("/places/\(place.id)/scene")
            var mesh: URL?

            if let meshMoment = loaded.meshMoment {
                let file = FileManager.default.temporaryDirectory.appending(path: "room-\(meshMoment).obj")

                if !FileManager.default.fileExists(atPath: file.path) {
                    try await API.current.download("/moments/\(meshMoment)/mesh").write(to: file)
                }

                mesh = file
            }

            var grouped: [(name: String, frames: [(moment: SceneMoment, frame: Frame)])] = []

            for moment in loaded.moments {
                for frame in moment.frames {
                    let name = frame.room ?? "Somewhere else"

                    if let index = grouped.firstIndex(where: { $0.name == name }) {
                        grouped[index].frames.append((moment, frame))
                    } else {
                        grouped.append((name, [(moment, frame)]))
                    }
                }
            }

            let roomLayers = grouped.enumerated().map { position, room in
                SceneLayer(path: [], frames: room.frames.map(\.frame), color: Self.roomColors[position % Self.roomColors.count])
            }
            let paths = loaded.moments.map { SceneLayer(path: $0.path, frames: [], color: .systemGray) }

            scene = try RoomScene.build(mesh: mesh, layers: roomLayers + paths)
            rooms = grouped
        } catch {
            problem = error.localizedDescription
        }
    }
}
