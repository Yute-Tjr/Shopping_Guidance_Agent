import SwiftUI

/// 商品详情页（PriceCat 风格）。
///
/// 布局自上而下：
/// - Hero 主图（占满宽度，4:3 比例，圆角）
/// - 品牌橙 chip
/// - 大字号商品标题
/// - 价格行：起价大红字 + 子标注"￥X 起"
/// - SKU 列表：每行白底卡片，左边属性、右边价格红字
struct ProductDetailView: View {
    let productID: String
    @EnvironmentObject private var env: AppEnvironment
    @State private var detail: ProductDetail?
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.Spacing.l) {
                if let detail {
                    heroImage(detail)
                    brandRow(detail)
                    Text(detail.title)
                        .font(Theme.Typo.display())
                        .foregroundStyle(Theme.Palette.textPrimary)
                    priceRow(detail)
                    Divider().background(Theme.Palette.border)
                    skuSection(detail)
                } else if let errorMessage {
                    errorView(errorMessage)
                } else {
                    loadingView
                }
            }
            .padding(Theme.Spacing.l)
        }
        .background(Theme.Palette.canvas.ignoresSafeArea())
        .navigationTitle(detail?.title ?? "商品详情")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Theme.Palette.canvas, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .task { await load() }
    }

    // MARK: - Sub views

    private func heroImage(_ detail: ProductDetail) -> some View {
        AsyncImage(url: detail.imageURL) { phase in
            switch phase {
            case .success(let image):
                image.resizable().scaledToFill()
            case .failure:
                Theme.Palette.chipSoft
                    .overlay(Image(systemName: "photo").foregroundStyle(Theme.Palette.textPlaceholder))
            default:
                Theme.Palette.chipSoft
            }
        }
        .aspectRatio(4.0/3.0, contentMode: .fit)
        .frame(maxWidth: .infinity)
        .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.hero, style: .continuous))
        .themeShadow(Theme.Shadow.card)
    }

    private func brandRow(_ detail: ProductDetail) -> some View {
        HStack(spacing: 6) {
            Text(detail.brand)
                .font(Theme.Typo.caption(.bold))
                .foregroundStyle(Theme.Palette.brand)
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
                .background(
                    Capsule().fill(Theme.Palette.chipSoft)
                )
            Text("\(detail.category) · \(detail.subCategory)")
                .font(Theme.Typo.caption())
                .foregroundStyle(Theme.Palette.textSecondary)
        }
    }

    private func priceRow(_ detail: ProductDetail) -> some View {
        let prices = detail.skus.map(\.price)
        let minP = prices.min() ?? detail.basePrice
        let maxP = prices.max() ?? detail.basePrice
        return HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(priceFormatted(minP))
                .font(.system(size: 28, weight: .heavy, design: .rounded))
                .foregroundStyle(Theme.Palette.priceHot)
            if maxP > minP {
                Text("起 · 最高 \(priceFormatted(maxP))")
                    .font(Theme.Typo.caption())
                    .foregroundStyle(Theme.Palette.textSecondary)
            } else {
                Text("起")
                    .font(Theme.Typo.caption())
                    .foregroundStyle(Theme.Palette.textSecondary)
            }
        }
    }

    private func skuSection(_ detail: ProductDetail) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.s) {
            Text("规格")
                .font(Theme.Typo.title())
                .foregroundStyle(Theme.Palette.textPrimary)
            ForEach(detail.skus) { sku in
                HStack {
                    Text(sku.properties.map { "\($0.key) \($0.value)" }.joined(separator: " · "))
                        .font(Theme.Typo.body())
                        .foregroundStyle(Theme.Palette.textPrimary)
                    Spacer()
                    Text(priceFormatted(sku.price))
                        .font(Theme.Typo.priceMd)
                        .foregroundStyle(Theme.Palette.priceHot)
                }
                .padding(.horizontal, Theme.Spacing.m)
                .padding(.vertical, 12)
                .background(
                    RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                        .fill(Theme.Palette.surface)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                        .stroke(Theme.Palette.border, lineWidth: 1)
                )
            }
        }
    }

    private func errorView(_ message: String) -> some View {
        VStack(spacing: Theme.Spacing.m) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 32))
                .foregroundStyle(Theme.Palette.priceHot.opacity(0.7))
            Text(message)
                .font(Theme.Typo.body())
                .foregroundStyle(Theme.Palette.textSecondary)
        }
        .padding(.top, 40)
        .frame(maxWidth: .infinity)
    }

    private var loadingView: some View {
        VStack(spacing: Theme.Spacing.m) {
            ProgressView()
                .tint(Theme.Palette.brand)
            Text("加载中…")
                .font(Theme.Typo.caption())
                .foregroundStyle(Theme.Palette.textSecondary)
        }
        .padding(.top, 80)
        .frame(maxWidth: .infinity)
    }

    // MARK: - Logic

    private func load() async {
        do {
            let api = APIClient(baseURL: env.baseURL)
            detail = try await api.fetchProductDetail(productID)
        } catch {
            errorMessage = "加载失败：\(error.localizedDescription)"
        }
    }

    private func priceFormatted(_ v: Double) -> String {
        if v.rounded() == v { return "¥\(Int(v))" }
        return String(format: "¥%.2f", v)
    }
}
