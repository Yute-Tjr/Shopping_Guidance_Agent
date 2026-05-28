import SwiftUI

/// Coupert 风格的商品卡片。
///
/// 视觉构图：
///
///     ┌─────────────────────────────────────────────────────┐
///     │ ┌──────┐  品牌 chip                ╮                │
///     │ │      │  商品标题（最多 2 行）   │  省¥17        │
///     │ │ 主图 │                          │  badge        │
///     │ │110×  │  ¥52  现价红             ╰────────────╯  │
///     │ │110   │  ¥69  原价划线灰  · 2 规格                │
///     │ └──────┘  ┌─ reason chip ───────────────┐         │
///     │           │ 泡沫绵密…                    │         │
///     │           └──────────────────────────────┘         │
///     └─────────────────────────────────────────────────────┘
///
/// 行为：
/// - 整张卡片是 Button，把选择交给聊天页统一导航。
/// - 主图走 `AsyncImage`，加载时浅橙占位（与背景一致，过渡更顺）。
/// - 价格逻辑：min == max 时只显示一个；不同则 min 大字红色 / max 划线灰。
///   "省 ¥xx" 用 max - min 算出，差为 0 时不渲染 badge。
struct ProductCardView: View {
    let card: ProductCard
    let onSelect: (ProductCard) -> Void

    init(card: ProductCard, onSelect: @escaping (ProductCard) -> Void = { _ in }) {
        self.card = card
        self.onSelect = onSelect
    }

    var body: some View {
        Button {
            onSelect(card)
        } label: {
            HStack(alignment: .top, spacing: Theme.Spacing.m) {
                imageBlock
                infoBlock
            }
            .padding(Theme.Spacing.m)
            .background(
                RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                    .fill(Theme.Palette.surface)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                    .stroke(Theme.Palette.border, lineWidth: 1)
            )
            .themeShadow(Theme.Shadow.card)
        }
        .buttonStyle(.plain)
    }

    // MARK: - Sub views

    private var imageBlock: some View {
        ZStack(alignment: .topTrailing) {
            AsyncImage(url: card.imageURL) { phase in
                switch phase {
                case .success(let image):
                    image.resizable().scaledToFill()
                case .failure:
                    Image(systemName: "photo")
                        .foregroundStyle(Theme.Palette.textPlaceholder)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                default:
                    Theme.Palette.chipSoft
                }
            }
            .frame(width: 110, height: 110)
            .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.chip + 2, style: .continuous))

            if let savedText = savedBadgeText {
                savedBadge(savedText)
                    .padding(.top, 6)
                    .padding(.trailing, 6)
            }
        }
    }

    private var infoBlock: some View {
        VStack(alignment: .leading, spacing: 6) {
            brandChip
            Text(card.title)
                .font(Theme.Typo.body(.semibold))
                .foregroundStyle(Theme.Palette.textPrimary)
                .lineLimit(2)
                .multilineTextAlignment(.leading)
            priceRow
            if !card.reason.isEmpty {
                reasonChip
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var brandChip: some View {
        HStack(spacing: 4) {
            Text(card.brand)
                .font(Theme.Typo.caption(.semibold))
                .foregroundStyle(Theme.Palette.brand)
            Text("·")
                .foregroundStyle(Theme.Palette.textPlaceholder)
            Text(card.category)
                .font(Theme.Typo.caption())
                .foregroundStyle(Theme.Palette.textSecondary)
        }
    }

    private var priceRow: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(priceFormatted(card.priceRange.min))
                .font(Theme.Typo.priceLg)
                .foregroundStyle(Theme.Palette.priceHot)
            if card.priceRange.max > card.priceRange.min {
                Text(priceFormatted(card.priceRange.max))
                    .font(Theme.Typo.body())
                    .foregroundStyle(Theme.Palette.textSecondary)
                    .strikethrough(true, color: Theme.Palette.textSecondary)
            }
            if !card.skus.isEmpty {
                Text("· \(card.skus.count) 规格")
                    .font(Theme.Typo.caption())
                    .foregroundStyle(Theme.Palette.textSecondary)
            }
        }
    }

    private var reasonChip: some View {
        Text(card.reason)
            .font(Theme.Typo.caption(.medium))
            .foregroundStyle(Theme.Palette.brand)
            .lineLimit(2)
            .multilineTextAlignment(.leading)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(
                RoundedRectangle(cornerRadius: Theme.Radius.chip, style: .continuous)
                    .fill(Theme.Palette.chipSoft)
            )
    }

    // MARK: - Saved badge

    private var savedBadgeText: String? {
        let diff = card.priceRange.max - card.priceRange.min
        guard diff > 0.01 else { return nil }
        if diff >= 100 {
            return "省 ¥\(Int(diff))"
        } else {
            return String(format: "省 ¥%.0f", diff)
        }
    }

    private func savedBadge(_ text: String) -> some View {
        Text(text)
            .font(Theme.Typo.caption(.bold))
            .foregroundStyle(Color.white)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(
                Capsule()
                    .fill(Theme.Palette.savingsGreen)
            )
            .themeShadow(.init(color: Theme.Palette.savingsGreen.opacity(0.35), radius: 6, x: 0, y: 2))
    }

    // MARK: - Helpers

    private func priceFormatted(_ v: Double) -> String {
        if v.rounded() == v {
            return "¥\(Int(v))"
        }
        return String(format: "¥%.2f", v)
    }
}
