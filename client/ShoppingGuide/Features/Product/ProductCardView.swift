import SwiftUI

/// 嵌入对话流的商品卡片。
///
/// 设计：
/// - 横向布局，80x80 主图 + 右侧信息块；
/// - 点击整张卡片 push `ProductDetailView`；
/// - 用 SwiftUI 自带 `AsyncImage`（Phase 3 不引 Kingfisher），加载时显示灰色占位；
/// - 价格区间用 `priceRange.min ~ priceRange.max`；min == max 时只显示一个；
/// - reason 用 secondary 色，限两行省略。
struct ProductCardView: View {
    let card: ProductCard

    var body: some View {
        NavigationLink {
            ProductDetailView(productID: card.productId)
        } label: {
            HStack(alignment: .top, spacing: 12) {
                AsyncImage(url: card.imageURL) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFill()
                    case .failure:
                        Image(systemName: "photo")
                            .foregroundStyle(.secondary)
                    default:
                        Color.gray.opacity(0.15)
                    }
                }
                .frame(width: 80, height: 80)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))

                VStack(alignment: .leading, spacing: 4) {
                    Text("\(card.brand) · \(card.category)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(card.title)
                        .font(.subheadline)
                        .lineLimit(2)
                        .foregroundStyle(.primary)
                    Text(priceString)
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.orange)
                    if !card.skus.isEmpty {
                        Text("\(card.skus.count) 种规格")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    if !card.reason.isEmpty {
                        Text(card.reason)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                            .padding(.top, 2)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color(.secondarySystemBackground))
            )
        }
        .buttonStyle(.plain)
    }

    private var priceString: String {
        if card.priceRange.min == card.priceRange.max {
            return String(format: "￥%.2f", card.priceRange.min)
        }
        return String(format: "￥%.2f - ￥%.2f", card.priceRange.min, card.priceRange.max)
    }
}
