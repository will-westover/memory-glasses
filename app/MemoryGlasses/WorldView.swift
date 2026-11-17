import MapKit
import SceneKit
import SwiftUI

struct WorldView: View {
    @State private var world: World?
    @State private var problem: String?
    @State private var camera: MapCameraPosition = .automatic
    @State private var openRoom: WorldRoom?
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
            .navigationDestination(for: WorldRoom.self) { CombinedRoomView(room: $0) }
            .navigationDestination(for: MomentLink.self) { MomentView(link: $0) }
            .navigationDestination(for: Fact.self) { FrameView(fact: $0) }
            .navigationDestination(item: $openRoom) { CombinedRoomView(room: $0) }
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

                if !world.rooms.isEmpty {
                    Section {
                        ForEach(world.rooms) { room in
                            NavigationLink(value: room) {
                                Label {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(room.label).font(.body.weight(.medium))
                                        Text(room.moments == 1 ? "1 moment, in 3D" : "\(room.moments) moments, stitched in 3D")
                                            .font(.subheadline)
                                            .foregroundStyle(.secondary)
                                    }
                                } icon: {
                                    Image(systemName: "cube.transparent")
                                }
                            }
                        }
                    } header: {
                        SectionTitle("Rooms").textCase(nil)
                    }
                }

                Section {
                    ForEach(world.moments.filter { $0.room == nil }) { moment in
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

    private func map(_ world: World) -> some View {
        Map(position: $camera) {
            ForEach(world.rooms) { room in
                Annotation(room.label, coordinate: CLLocationCoordinate2D(latitude: room.lat, longitude: room.lon)) {
                    Button { openRoom = room } label: {
                        Image(systemName: "house.fill")
                            .font(.caption)
                            .foregroundStyle(Color(.systemBackground))
                            .frame(width: 30, height: 30)
                            .background(Color.primary, in: Circle())
                    }
                }
            }

            ForEach(world.moments.filter { $0.room == nil }) { moment in
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

struct CombinedRoomView: View {
    static let colors: [UIColor] = [.systemOrange, .systemBlue, .systemGreen, .systemPink, .systemPurple, .systemTeal]

    let room: WorldRoom

    @State private var data: RoomSceneData?
    @State private var scene: SCNScene?
    @State private var problem: String?
    @State private var opened: Fact?

    var body: some View {
        VStack(spacing: 0) {
            if let scene, let data {
                RoomScene(scene: scene) { layer, index in open(data, layer, index) }

                legend(data)
            } else if let problem {
                ContentUnavailableView("Couldn't load the room", systemImage: "cube.transparent", description: Text(problem))
            } else {
                ProgressView("Stitching the room…")
                    .frame(maxHeight: .infinity)
            }
        }
        .navigationTitle(room.label)
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(item: $opened) { FrameView(fact: $0) }
        .task { await load() }
    }

    private func legend(_ data: RoomSceneData) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 14) {
                ForEach(Array(data.moments.enumerated()), id: \.offset) { position, moment in
                    HStack(spacing: 6) {
                        Circle()
                            .fill(Color(Self.colors[position % Self.colors.count]))
                            .frame(width: 10, height: 10)

                        Text(moment.startedAt.formatted(.dateTime.month().day().hour().minute()))
                            .font(.caption)
                    }
                }
            }
            .padding(16)
        }
        .background(.bar)
    }

    private func open(_ data: RoomSceneData, _ layer: Int, _ index: Int) {
        guard data.moments.indices.contains(layer), data.moments[layer].frames.indices.contains(index) else { return }

        let moment = data.moments[layer]
        let frame = moment.frames[index]

        opened = Fact(id: -frame.id, kind: "frame", at: frame.at, momentId: moment.id, offsetSeconds: frame.offsetSeconds,
                      text: frame.summary, image: frame.image)
    }

    private func load() async {
        do {
            let loaded: RoomSceneData = try await API.current.get("/rooms/\(room.id)/scene")
            var mesh: URL?

            if let meshMoment = loaded.meshMoment {
                let file = FileManager.default.temporaryDirectory.appending(path: "room-\(meshMoment).obj")

                if !FileManager.default.fileExists(atPath: file.path) {
                    try await API.current.image("/moments/\(meshMoment)/mesh").write(to: file)
                }

                mesh = file
            }

            let layers = loaded.moments.enumerated().map { position, moment in
                SceneLayer(path: moment.path, frames: moment.frames, color: Self.colors[position % Self.colors.count])
            }

            scene = try RoomScene.build(mesh: mesh, layers: layers)
            data = loaded
        } catch {
            problem = error.localizedDescription
        }
    }
}
