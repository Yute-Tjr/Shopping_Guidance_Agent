import Foundation

/// POST /api/v1/upload/image —— multipart 上传，返回 image_id。
///
/// 与 APIClient / ChatTransport 分文件：上传失败和 chat 失败的错误处理独立，
/// 避免一个 service 类承担太多 endpoint。
///
/// 注意：后端 503 + `fallback_text_only: true` 表示 vision API 限流，
/// 客户端可以丢弃图片只发文字继续；其它非 200 当作硬错。
public final class UploadService: @unchecked Sendable {

    private let session: URLSession
    private let baseURL: URL

    public init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    public func upload(image data: Data, filename: String = "upload.jpg") async throws -> String {
        let url = baseURL.appendingPathComponent("/api/v1/upload/image")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let boundary = "Boundary-\(UUID().uuidString)"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = makeMultipartBody(
            boundary: boundary, fieldName: "file", filename: filename,
            mime: "image/jpeg", data: data,
        )

        let (responseData, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw UploadError.invalidResponse }
        if http.statusCode == 200 {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            let parsed = try decoder.decode(UploadResponse.self, from: responseData)
            return parsed.imageId
        }
        // 503 降级：服务器告知可以纯文本继续
        if http.statusCode == 503 {
            let msg = (try? JSONDecoder().decode(ServerErrorEnvelope.self, from: responseData))
                .flatMap { $0.detail?.message } ?? "图片识别繁忙"
            throw UploadError.degraded(message: msg)
        }
        // 其它错误：尽量从 body 读 detail（FastAPI 422/413/415 detail 是字符串）
        let detail = (try? JSONDecoder().decode(SimpleErrorBody.self, from: responseData))?.detail
            ?? "上传失败"
        throw UploadError.server(status: http.statusCode, message: detail)
    }

    private func makeMultipartBody(
        boundary: String, fieldName: String, filename: String, mime: String, data: Data,
    ) -> Data {
        var body = Data()
        let lineBreak = "\r\n"
        func append(_ s: String) { body.append(s.data(using: .utf8)!) }
        append("--\(boundary)\(lineBreak)")
        append("Content-Disposition: form-data; name=\"\(fieldName)\"; filename=\"\(filename)\"\(lineBreak)")
        append("Content-Type: \(mime)\(lineBreak)\(lineBreak)")
        body.append(data)
        append(lineBreak)
        append("--\(boundary)--\(lineBreak)")
        return body
    }
}

public struct UploadResponse: Codable, Equatable, Sendable {
    public let imageId: String
    public let previewUrl: String?
}

public enum UploadError: Error, LocalizedError, Equatable {
    case invalidResponse
    case server(status: Int, message: String)
    case degraded(message: String)

    public var errorDescription: String? {
        switch self {
        case .invalidResponse: return "服务返回异常"
        case .server(_, let m): return m
        case .degraded(let m): return m
        }
    }
}

// MARK: - Wire formats

private struct SimpleErrorBody: Decodable {
    let detail: String?
}

private struct ServerErrorEnvelope: Decodable {
    let detail: DegradedDetail?
    struct DegradedDetail: Decodable {
        let degraded: Bool?
        let fallbackTextOnly: Bool?
        let message: String?
        enum CodingKeys: String, CodingKey {
            case degraded
            case fallbackTextOnly = "fallback_text_only"
            case message
        }
    }
}
