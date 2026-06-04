import SwiftUI

public struct StarterPrompt: Identifiable, Equatable, Sendable {
    public enum AccentRole: String, CaseIterable, Sendable {
        case skincare
        case audio
        case compare
        case gift
    }

    public let id: String
    public let text: String
    public let symbolName: String
    public let accentRole: AccentRole

    public init(text: String, symbolName: String, accentRole: AccentRole) {
        self.id = text
        self.text = text
        self.symbolName = symbolName
        self.accentRole = accentRole
    }

    public static let defaultItems: [StarterPrompt] = [
        .init(text: "推荐一款适合油皮的洗面奶", symbolName: "drop.fill", accentRole: .skincare),
        .init(text: "200 元以下的蓝牙耳机", symbolName: "headphones", accentRole: .audio),
        .init(text: "对比一下兰蔻和雅诗兰黛的精华", symbolName: "rectangle.2.swap", accentRole: .compare),
        .init(text: "送女朋友的口红选什么色号", symbolName: "gift.fill", accentRole: .gift),
    ]
}

extension StarterPrompt.AccentRole {
    var color: Color {
        switch self {
        case .skincare:
            Theme.Palette.savingsGreen
        case .audio:
            Theme.Palette.brandSoft
        case .compare:
            Theme.Palette.highlight
        case .gift:
            Theme.Palette.priceHot
        }
    }

    var softFill: Color {
        color.opacity(self == .audio ? 0.11 : 0.16)
    }
}
