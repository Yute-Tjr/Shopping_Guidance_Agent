import Foundation

/// 非流式 HTTP 调用 + 流式请求构造。
///
/// SSE 长连接不能用 `dataTask(completionHandler:)`——那种 API 等整段 Body
/// 才返回。流式请求由 `StreamingClient` 用 `URLSessionDataDelegate` 接管，
/// 这里只负责按规约把 `URLRequest` 拼出来。
public final class APIClient: @unchecked Sendable {

    public let baseURL: URL
    private let session: URLSession

    public init(baseURL: URL) {
        self.baseURL = baseURL
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: cfg)
    }

    /// 构造 POST /api/v1/chat/stream 的请求。SSE 头必须 `Accept: text/event-stream`，
    /// 后端 sse-starlette 会按这个 Content-Type 流式返回。
    public func buildChatStreamRequest(message: String, sessionID: String?, imageID: String? = nil) -> URLRequest {
        var req = URLRequest(url: baseURL.appendingPathComponent(Endpoints.chatStream))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        var body: [String: Any] = ["message": message]
        if let sessionID { body["session_id"] = sessionID }
        if let imageID { body["image_id"] = imageID }
        req.httpBody = try? JSONSerialization.data(withJSONObject: body, options: [])
        return req
    }

    /// GET /api/v1/products/{id}，详情页用。
    public func fetchProductDetail(_ productID: String) async throws -> ProductDetail {
        let url = baseURL.appendingPathComponent(Endpoints.productDetail(productID))
        let (data, resp) = try await session.data(from: url)
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            throw APIError.httpStatus(http.statusCode)
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(ProductDetail.self, from: data)
    }
}

public enum APIError: Error, Equatable {
    case httpStatus(Int)
}

/// 详情页响应模型，比 `ProductCard` 多 sub_category / base_price / raw。
public struct ProductDetail: Codable, Equatable, Sendable {
    public let productId: String
    public let title: String
    public let brand: String
    public let category: String
    public let subCategory: String
    public let basePrice: Double
    public let imageURL: URL
    public let skus: [ProductCard.SKU]

    private enum CodingKeys: String, CodingKey {
        case productId, title, brand, category, subCategory, basePrice
        case imageURL = "imageUrl"   // convertFromSnakeCase 把 image_url 转成 imageUrl
        case skus
    }
}
