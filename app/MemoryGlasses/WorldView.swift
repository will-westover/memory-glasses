import MapKit
import SceneKit
import SwiftUI

struct WorldView: View {
    @State private var world: World?
    @State private var problem: String?
    @State private var camera: MapCameraPosition = .automatic
    @State private var openSpace: WorldSpace?
    @State private var openMoment: MomentLink?

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
            .navigationDestination(for: WorldSpace.self) { SpaceSceneView(space: $0) }
            .navigationDestination(for: MomentLink.self) { MomentView(link: $0) }
            .navigationDestination(for: Fact.self) { FrameView(fact: $0) }
            .navigationDestination(item: $openSpace) { SpaceSceneView(space: $0) }
            .navigationDestination(item: $openMoment) { MomentView(link: $0) }
            .task { await load() }
        }
    }

    @ViewBuilder
    private func content(_ world: World) -> some View {
        if world.moments.isEmpty {
            ContentUnavailableView("Nowhere yet", systemImage: "map",
                                   description: Text("Moments recorded with a location appear here."))
        } else {
            List {
                Section {
                    map(world)
                        .frame(height: 300)
                        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                        .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 8, trailing: 16))
                        .listRowSeparator(.hidden)
                }

                if !world.spaces.isEmpty {
                    Section {
                        ForEach(world.spaces) { space in
                            if space.hasMap {
                                NavigationLink(value: space) { spaceRow(space, icon: "cube.transparent") }
                            } else {
                                spaceRow(space, icon: "house")
                            }
                        }
                    } header: {
                        SectionTitle("Places").textCase(nil)
                    }
                }

                Section {
                    ForEach(world.moments.filter { $0.space == nil }) { moment in
                        NavigationLink(value: MomentLink(id: moment.id, startedAt: moment.startedAt)) {
                            Label(moment.startedAt.formatted(.dateTime.weekday(.abbreviated).month().day().hour().minute()),
                                  systemImage: sourceIcon(moment.source).symbol)
                        }
                    }
                } header: {
                    SectionTitle("Other moments with a location").textCase(nil)
                }
            }
            .listStyle(.plain)
            .refreshable { await load() }
        }
    }

    private func spaceRow(_ space: WorldSpace, icon: String) -> some View {
        Label {
            VStack(alignment: .leading, spacing: 2) {
                Text(space.label).font(.body.weight(.medium))
                Text(space.rooms.isEmpty ? momentCount(space.moments) : space.rooms.joined(separator: ", "))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        } icon: {
            Image(systemName: icon)
        }
    }

    private func map(_ world: World) -> some View {
        Map(position: $camera) {
            ForEach(world.spaces) { space in
                Annotation(space.label, coordinate: CLLocationCoordinate2D(latitude: space.lat, longitude: space.lon)) {
                    Button { if space.hasMap { openSpace = space } } label: {
                        Image(systemName: "house.fill")
                            .font(.caption)
                            .foregroundStyle(Color(.systemBackground))
                            .frame(width: 30, height: 30)
                            .background(Color.primary, in: Circle())
                    }
                }
            }

            ForEach(world.moments.filter { $0.space == nil }) { moment in
                Annotation("", coordinate: CLLocationCoordinate2D(latitude: moment.lat, longitude: moment.lon)) {
                    Button { openMoment = MomentLink(id: moment.id, startedAt: moment.startedAt) } label: {
                        FrameImage(path: moment.cover)
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

struct SpaceSceneView: View {
    static let colors: [UIColor] = [.systemOrange, .systemBlue, .systemGreen, .systemPink, .systemPurple, .systemTeal,
                                    .systemYellow, .systemIndigo]

    let space: WorldSpace

    @State private var scene: SCNScene?
    @State private var rooms: [(name: String, frames: [(moment: SceneMoment, frame: Frame)])] = []
    @State private var problem: String?
    @State private var opened: Fact?

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
        .navigationTitle(space.label)
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(item: $opened) { FrameView(fact: $0) }
        .task { await load() }
    }

    private var legend: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 14) {
                ForEach(Array(rooms.enumerated()), id: \.offset) { position, room in
                    HStack(spacing: 6) {
                        Circle()
                            .fill(Color(Self.colors[position % Self.colors.count]))
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

        opened = Fact(id: -frame.id, kind: "frame", at: frame.at, momentId: moment.id, offsetSeconds: frame.offsetSeconds,
                      text: frame.summary, image: frame.image)
    }

    private func load() async {
        do {
            let loaded: SpaceSceneData = try await API.current.get("/spaces/\(space.id)/scene")
            var mesh: URL?

            if let meshMoment = loaded.meshMoment {
                let file = FileManager.default.temporaryDirectory.appending(path: "room-\(meshMoment).obj")

                if !FileManager.default.fileExists(atPath: file.path) {
                    try await API.current.image("/moments/\(meshMoment)/mesh").write(to: file)
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
                SceneLayer(path: [], frames: room.frames.map(\.frame), color: Self.colors[position % Self.colors.count])
            }
            let paths = loaded.moments.map { SceneLayer(path: $0.path, frames: [], color: .systemGray) }

            scene = try RoomScene.build(mesh: mesh, layers: roomLayers + paths)
            rooms = grouped
        } catch {
            problem = error.localizedDescription
        }
    }
}
