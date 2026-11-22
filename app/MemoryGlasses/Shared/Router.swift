import SwiftUI

enum AppTab: Hashable {
    case ask, timeline, world, stats, glasses
}

struct SourceTrail: Equatable {
    let sources: [Fact]
    let words: [Int: Set<String>]
    let fromSearch: Bool
    var index: Int

    var current: Fact { sources[index] }
}

@Observable
final class Router {
    var tab: AppTab = .ask
    var trail: SourceTrail?
    var timelinePath = NavigationPath()
    var focusChangeCount = 0

    var focus: Fact? { trail?.current }

    var focusWords: Set<String> {
        guard let trail else { return [] }

        return trail.words[trail.index] ?? []
    }

    func open(_ fact: Fact, answer: Answer? = nil, question: String = "") {
        let sources = answer?.sources ?? [fact]
        let index = sources.firstIndex(of: fact) ?? 0
        let words = Dictionary(uniqueKeysWithValues: sources.indices.map { position in
            (position, KeywordMatch.sharedKeywords(source: sources[position].text,
                                        context: KeywordMatch.sentencesCiting(position + 1, in: answer?.answer ?? "") + " " + question))
        })

        show(SourceTrail(sources: sources, words: words, fromSearch: false, index: index))
    }

    func openSearch(_ results: [Fact], at index: Int, query: String) {
        let words = Dictionary(uniqueKeysWithValues: results.indices.map { position in
            (position, KeywordMatch.sharedKeywords(source: results[position].text, context: query))
        })

        show(SourceTrail(sources: results, words: words, fromSearch: true, index: index))
    }

    func stepSource(by change: Int) {
        guard var trail, trail.sources.indices.contains(trail.index + change) else { return }

        trail.index += change
        show(trail)
    }

    func backToChat() {
        trail = nil
        timelinePath = NavigationPath()
        tab = .ask
    }

    func clearTrail() {
        trail = nil
    }

    func citedSources(in momentId: String) -> [(number: Int, fact: Fact)] {
        guard let trail else { return [] }

        return trail.sources.enumerated().filter { $0.element.momentId == momentId }.map { ($0.offset + 1, $0.element) }
    }

    private func show(_ next: SourceTrail) {
        let previous = trail?.current.momentId

        trail = next
        focusChangeCount += 1
        tab = .timeline

        if previous != next.current.momentId || timelinePath.isEmpty {
            timelinePath = NavigationPath([next.current.moment])
        }
    }
}

enum KeywordMatch {
    static let stopWords: Set<String> = [
        "about", "after", "again", "also", "around", "been", "before", "being", "could", "does", "doing", "down",
        "each", "from", "have", "having", "here", "into", "just", "like", "more", "most", "near", "next", "only",
        "other", "over", "record", "recording", "said", "same", "seen", "shows", "some", "than", "that", "their",
        "them", "then", "there", "these", "they", "this", "those", "through", "very", "visible", "were", "what",
        "when", "where", "which", "while", "with", "would", "your", "yours", "today",
    ]

    static func keywords(in text: String) -> Set<String> {
        Set(text.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init)
            .filter { $0.count >= 4 && !stopWords.contains($0) })
    }

    static func sharedKeywords(source: String, context: String) -> Set<String> {
        keywords(in: source).intersection(keywords(in: context))
    }

    static func sentencesCiting(_ number: Int, in answer: String) -> String {
        let moved = answer.replacing(/([.!?])((?:\s*\[\d+\])+)/) { "\($0.2)\($0.1)" }
        let sentences = moved.split(whereSeparator: { ".!?\n".contains($0) })
        let cited = sentences.filter { $0.contains("[\(number)]") }

        return (cited.isEmpty ? sentences : cited).joined(separator: " ")
    }

    static func emphasize(_ text: String, _ marked: Set<String>) -> AttributedString {
        var result = AttributedString(text)

        guard !marked.isEmpty else { return result }

        var start = text.startIndex

        while start < text.endIndex {
            guard let wordStart = text[start...].firstIndex(where: { $0.isLetter || $0.isNumber }) else { break }

            let wordEnd = text[wordStart...].firstIndex(where: { !$0.isLetter && !$0.isNumber }) ?? text.endIndex

            if marked.contains(text[wordStart..<wordEnd].lowercased()),
               let low = AttributedString.Index(wordStart, within: result),
               let high = AttributedString.Index(wordEnd, within: result) {
                result[low..<high].inlinePresentationIntent = .stronglyEmphasized
                result[low..<high].backgroundColor = Color.yellow.opacity(0.35)
            }

            start = wordEnd
        }

        return result
    }
}

private struct OpenSourceKey: EnvironmentKey {
    static let defaultValue: (Fact) -> Void = { _ in }
}

extension EnvironmentValues {
    var openSource: (Fact) -> Void {
        get { self[OpenSourceKey.self] }
        set { self[OpenSourceKey.self] = newValue }
    }
}
