import Foundation

/// 商品卡片——对应后端 SSE 的 `event: product_card` payload。
/// 字段与 `server/app/schemas/chat.py:ProductCardEvent` 对齐；解码时使用
/// `JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase` 自动把 product_id 等
/// 转成 productId。
public struct ProductCard: Identifiable, Codable, Equatable, Sendable {
    public var id: String { productId }
    public let productId: String
    public let title: String
    public let brand: String
    public let category: String
    public let imageURL: URL
    public let priceRange: PriceRange
    public let skus: [SKU]
    public let reason: String

    public struct PriceRange: Codable, Equatable, Sendable {
        public let min: Double
        public let max: Double

        public init(min: Double, max: Double) {
            self.min = min
            self.max = max
        }
    }

    public struct SKU: Codable, Equatable, Identifiable, Sendable {
        public var id: String { skuId }
        public let skuId: String
        public let properties: [String: String]
        public let price: Double

        public init(skuId: String, properties: [String: String], price: Double) {
            self.skuId = skuId
            self.properties = properties
            self.price = price
        }
    }

    public init(
        productId: String,
        title: String,
        brand: String,
        category: String,
        imageURL: URL,
        priceRange: PriceRange,
        skus: [SKU],
        reason: String
    ) {
        self.productId = productId
        self.title = title
        self.brand = brand
        self.category = category
        self.imageURL = imageURL
        self.priceRange = priceRange
        self.skus = skus
        self.reason = reason
    }

    private enum CodingKeys: String, CodingKey {
        case productId
        case title
        case brand
        case category
        case imageURL = "imageUrl"   // .convertFromSnakeCase 把 image_url 转成 imageUrl
        case priceRange
        case skus
        case reason
    }
}

public struct ProductNavigationDestination: Identifiable, Hashable, Sendable {
    public var id: String { productID }
    public let productID: String

    public init(productID: String) {
        self.productID = productID
    }
}

public struct ProductNavigationSelection: Equatable, Sendable {
    public var destination: ProductNavigationDestination?

    public init(destination: ProductNavigationDestination? = nil) {
        self.destination = destination
    }

    public mutating func select(productID: String) {
        destination = ProductNavigationDestination(productID: productID)
    }

    public mutating func clear() {
        destination = nil
    }
}
