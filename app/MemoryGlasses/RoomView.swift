import SceneKit
import SwiftUI

struct RoomView: View {
    let momentId: String
    let frames: [Frame]
    let path: [[Double]]
    let pick: (Int) -> Void

    @Environment(\.dismiss) private var dismiss

    @State private var scene: SCNScene?
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            Group {
                if let scene {
                    RoomScene(scene: scene) { index in
                        pick(index)
                        dismiss()
                    }
                    .ignoresSafeArea(edges: .bottom)
                } else if let problem {
                    ContentUnavailableView("Couldn't load the room", systemImage: "cube.transparent",
                                           description: Text(problem))
                } else {
                    ProgressView("Loading the room…")
                }
            }
            .navigationTitle("Room")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } }
            }
            .safeAreaInset(edge: .bottom) {
                Text("Drag to turn, pinch to zoom, tap a frame to open it.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.bottom, 8)
            }
            .task { await load() }
        }
    }

    private func load() async {
        do {
            let data = try await API.current.image("/moments/\(momentId)/mesh")
            let file = FileManager.default.temporaryDirectory.appending(path: "room-\(momentId).obj")
            try data.write(to: file)

            scene = try RoomScene.build(mesh: file, frames: frames, path: path)
        } catch {
            problem = error.localizedDescription
        }
    }
}

struct RoomScene: UIViewRepresentable {
    let scene: SCNScene
    let pick: (Int) -> Void

    static func build(mesh: URL, frames: [Frame], path: [[Double]]) throws -> SCNScene {
        let scene = try SCNScene(url: mesh)

        let wall = SCNMaterial()
        wall.diffuse.contents = UIColor.secondaryLabel.withAlphaComponent(0.35)
        wall.isDoubleSided = true
        wall.lightingModel = .lambert

        scene.rootNode.enumerateChildNodes { node, _ in
            node.geometry?.materials = [wall]
        }

        if path.count > 1 {
            scene.rootNode.addChildNode(line(through: path.map { SCNVector3($0[1], $0[2], $0[3]) }))
        }

        for (index, frame) in frames.enumerated() {
            guard let pose = frame.position else { continue }

            scene.rootNode.addChildNode(marker(index: index, at: SCNVector3(pose.x, pose.y, pose.z), yaw: pose.yaw))
        }

        let light = SCNNode()
        light.light = SCNLight()
        light.light?.type = .ambient
        light.light?.intensity = 900
        scene.rootNode.addChildNode(light)

        scene.rootNode.addChildNode(camera(looking: scene.rootNode.boundingBox))

        return scene
    }

    static func camera(looking box: (min: SCNVector3, max: SCNVector3)) -> SCNNode {
        let centre = SCNVector3((box.min.x + box.max.x) / 2, (box.min.y + box.max.y) / 2, (box.min.z + box.max.z) / 2)
        let size = max(box.max.x - box.min.x, box.max.z - box.min.z, 1)

        let node = SCNNode()
        node.name = "overview"
        node.camera = SCNCamera()
        node.camera?.zNear = 0.05
        node.position = SCNVector3(centre.x, centre.y + size * 0.9, centre.z + size * 0.9)
        node.look(at: centre)

        return node
    }

    static func line(through points: [SCNVector3]) -> SCNNode {
        let source = SCNGeometrySource(vertices: points)
        let indices = (0..<Int32(points.count - 1)).flatMap { [$0, $0 + 1] }
        let element = SCNGeometryElement(indices: indices, primitiveType: .line)

        let geometry = SCNGeometry(sources: [source], elements: [element])
        let material = SCNMaterial()
        material.diffuse.contents = UIColor.label
        material.lightingModel = .constant
        geometry.materials = [material]

        return SCNNode(geometry: geometry)
    }

    static func marker(index: Int, at position: SCNVector3, yaw: Double) -> SCNNode {
        let cone = SCNCone(topRadius: 0, bottomRadius: 0.06, height: 0.16)
        let material = SCNMaterial()
        material.diffuse.contents = UIColor.systemOrange
        material.lightingModel = .constant
        cone.materials = [material]

        let node = SCNNode(geometry: cone)
        node.name = "frame-\(index)"
        node.position = position
        node.eulerAngles = SCNVector3(Float.pi / 2, Float(yaw), 0)

        return node
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(pick: pick)
    }

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.scene = scene
        view.pointOfView = scene.rootNode.childNode(withName: "overview", recursively: false)
        view.allowsCameraControl = true
        view.autoenablesDefaultLighting = true
        view.backgroundColor = .systemBackground
        view.addGestureRecognizer(UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.tapped(_:))))

        return view
    }

    func updateUIView(_ view: SCNView, context: Context) {}

    final class Coordinator: NSObject {
        let pick: (Int) -> Void

        init(pick: @escaping (Int) -> Void) {
            self.pick = pick
        }

        @objc func tapped(_ gesture: UITapGestureRecognizer) {
            guard let view = gesture.view as? SCNView else { return }

            let hits = view.hitTest(gesture.location(in: view), options: [.searchMode: SCNHitTestSearchMode.all.rawValue])

            if let name = hits.compactMap({ $0.node.name }).first(where: { $0.hasPrefix("frame-") }),
               let index = Int(name.dropFirst("frame-".count)) {
                pick(index)
            }
        }
    }
}
