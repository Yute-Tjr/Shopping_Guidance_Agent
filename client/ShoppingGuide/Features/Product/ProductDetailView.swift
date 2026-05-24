import SwiftUI

/// 详情页：进入时 GET /api/v1/products/{id}，渲染主图 + 标题 + 全部 SKU。
struct ProductDetailView: View {
    let productID: String
    @EnvironmentObject private var env: AppEnvironment
    @State private var detail: ProductDetail?
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let detail {
                    AsyncImage(url: detail.imageURL) { phase in
                        switch phase {
                        case .success(let image):
                            image.resizable().scaledToFit()
                        case .failure:
                            Color.gray.opacity(0.1)
                                .frame(height: 240)
                                .overlay(Image(systemName: "photo").foregroundStyle(.secondary))
                        default:
                            Color.gray.opacity(0.1).frame(height: 240)
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

                    Text(detail.title)
                        .font(.title3.weight(.semibold))
                    HStack {
                        Text(detail.brand)
                            .font(.caption)
                            .padding(.horizontal, 8).padding(.vertical, 3)
                            .background(Color(.tertiarySystemBackground), in: Capsule())
                        Text("\(detail.category) · \(detail.subCategory)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Text(String(format: "￥%.2f 起", detail.basePrice))
                        .font(.title3.weight(.bold))
                        .foregroundStyle(.orange)

                    Divider()
                    Text("SKU").font(.headline)
                    ForEach(detail.skus) { sku in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(sku.properties.map { "\($0.key) \($0.value)" }.joined(separator: " · "))
                                .font(.subheadline)
                            Text(String(format: "￥%.2f", sku.price))
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(.orange)
                        }
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10))
                    }
                } else if let errorMessage {
                    VStack(spacing: 12) {
                        Image(systemName: "exclamationmark.triangle")
                            .font(.largeTitle).foregroundStyle(.secondary)
                        Text(errorMessage).foregroundStyle(.secondary)
                    }
                    .padding(.top, 40)
                } else {
                    ProgressView("加载中…")
                        .frame(maxWidth: .infinity, minHeight: 200)
                }
            }
            .padding()
        }
        .navigationTitle(detail?.title ?? "商品详情")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    private func load() async {
        do {
            let api = APIClient(baseURL: env.baseURL)
            detail = try await api.fetchProductDetail(productID)
        } catch {
            errorMessage = "加载失败：\(error.localizedDescription)"
        }
    }
}
