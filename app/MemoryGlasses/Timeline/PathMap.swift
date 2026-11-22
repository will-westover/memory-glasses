import SwiftUI

struct PathMap: View {
    let points: [[Double]]
    let frames: [Frame]
    @Binding var index: Int

    var body: some View {
        GeometryReader { space in
            let projection = PathProjection(points: points, size: space.size)

            ZStack {
                Canvas { context, _ in
                    var line = Path()

                    for (position, point) in points.enumerated() {
                        let at = projection.point(point[1], point[3])
                        position == 0 ? line.move(to: at) : line.addLine(to: at)
                    }

                    context.stroke(line, with: .color(.secondary), style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))

                    for (position, frame) in frames.enumerated() {
                        guard let pose = frame.position, position != index else { continue }

                        let at = projection.point(pose.x, pose.z)
                        context.fill(Path(ellipseIn: CGRect(x: at.x - 3.5, y: at.y - 3.5, width: 7, height: 7)), with: .color(.primary.opacity(0.45)))
                    }

                    if frames.indices.contains(index), let pose = frames[index].position {
                        let at = projection.point(pose.x, pose.z)
                        var arrow = Path()
                        arrow.move(to: at)
                        arrow.addLine(to: CGPoint(x: at.x + sin(pose.yaw) * 22, y: at.y + cos(pose.yaw) * 22))

                        context.stroke(arrow, with: .color(.primary), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                        context.fill(Path(ellipseIn: CGRect(x: at.x - 7, y: at.y - 7, width: 14, height: 14)), with: .color(.primary))
                    }
                }
            }
            .contentShape(Rectangle())
            .onTapGesture { tap in jump(to: tap, projection: projection) }
        }
        .aspectRatio(4 / 3, contentMode: .fit)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private func jump(to tap: CGPoint, projection: PathProjection) {
        let nearest = frames.indices
            .compactMap { position -> (Int, CGFloat)? in
                guard let pose = frames[position].position else { return nil }

                let at = projection.point(pose.x, pose.z)

                return (position, hypot(at.x - tap.x, at.y - tap.y))
            }
            .min { $0.1 < $1.1 }

        if let nearest, nearest.1 < 40 {
            withAnimation(.snappy) { index = nearest.0 }
        }
    }
}

struct PathProjection {
    let minX: Double
    let minZ: Double
    let scale: Double
    let offset: CGSize

    init(points: [[Double]], size: CGSize, margin: Double = 28) {
        let xs = points.map { $0[1] }
        let zs = points.map { $0[3] }

        minX = xs.min() ?? 0
        minZ = zs.min() ?? 0

        let width = max((xs.max() ?? 1) - minX, 0.5)
        let depth = max((zs.max() ?? 1) - minZ, 0.5)

        scale = min((size.width - margin * 2) / width, (size.height - margin * 2) / depth)
        offset = CGSize(width: (size.width - width * scale) / 2, height: (size.height - depth * scale) / 2)
    }

    func point(_ x: Double, _ z: Double) -> CGPoint {
        CGPoint(x: offset.width + (x - minX) * scale, y: offset.height + (z - minZ) * scale)
    }
}
