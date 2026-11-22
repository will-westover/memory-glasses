import SwiftUI

struct SearchResults: View {
    let query: String
    let results: [Fact]
    let moments: [MomentSummary]
    let searching: Bool

    @Environment(Router.self) private var router

    var body: some View {
        if results.isEmpty {
            if searching {
                ProgressView()
            } else {
                ContentUnavailableView.search(text: query)
            }
        } else {
            List {
                ForEach(groups, id: \.momentId) { group in
                    Section {
                        ForEach(group.hits, id: \.fact.id) { hit in
                            Button { router.openSearch(results, at: hit.position, query: query) } label: {
                                HitRow(fact: hit.fact, marked: KeywordMatch.sharedKeywords(source: hit.fact.text, context: query))
                            }
                            .buttonStyle(.plain)
                        }
                    } header: {
                        header(group)
                    }
                }
            }
            .listStyle(.plain)
        }
    }

    private struct HitGroup {
        let momentId: String
        let hits: [(position: Int, fact: Fact)]
    }

    private var groups: [HitGroup] {
        var order: [String] = []
        var hits: [String: [(position: Int, fact: Fact)]] = [:]

        for (position, fact) in results.enumerated() {
            if hits[fact.momentId] == nil { order.append(fact.momentId) }

            hits[fact.momentId, default: []].append((position, fact))
        }

        return order.map { HitGroup(momentId: $0, hits: hits[$0] ?? []) }
    }

    private func header(_ group: HitGroup) -> some View {
        let moment = moments.first { $0.id == group.momentId }
        let start = moment?.startedAt ?? group.hits[0].fact.moment.startedAt

        return VStack(alignment: .leading, spacing: 6) {
            Text(start.formatted(.dateTime.weekday(.wide).month().day().hour().minute()))
                .font(.footnote.weight(.medium))
                .foregroundStyle(.secondary)
                .textCase(nil)

            if let moment {
                MarkerStrip(duration: moment.durationSeconds,
                            marks: group.hits.map { ($0.position + 1, $0.fact.offsetSeconds) })
            }
        }
    }
}

struct HitRow: View {
    let fact: Fact
    let marked: Set<String>

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            FrameImage(path: fact.image)
                .frame(width: 64, height: 48)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text(fact.at.formatted(date: .omitted, time: .shortened))
                    .font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(.secondary)

                Text(KeywordMatch.emphasize(fact.text, marked))
                    .font(.subheadline)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)
            }
        }
        .padding(.vertical, 2)
    }
}
