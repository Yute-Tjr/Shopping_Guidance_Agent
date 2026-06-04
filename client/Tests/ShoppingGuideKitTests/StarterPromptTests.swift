import Testing
@testable import ShoppingGuideKit

@Suite("Starter prompts")
struct StarterPromptTests {
    @Test func defaultItemsCarryDistinctIconsAndAccentRoles() {
        let items = StarterPrompt.defaultItems

        #expect(items.map(\.text) == [
            "推荐一款适合油皮的洗面奶",
            "200 元以下的蓝牙耳机",
            "对比一下兰蔻和雅诗兰黛的精华",
            "送女朋友的口红选什么色号",
        ])
        #expect(items.map(\.symbolName) == [
            "drop.fill",
            "headphones",
            "rectangle.2.swap",
            "gift.fill",
        ])
        #expect(Set(items.map(\.accentRole)).count == items.count)
    }
}
