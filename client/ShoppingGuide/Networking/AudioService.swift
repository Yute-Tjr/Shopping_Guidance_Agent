import Foundation

public final class AudioService: @unchecked Sendable {
    private let baseURL: URL
    private let session: URLSession

    public init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    public func transcribe(pcm data: Data) async throws -> String {
        let url = baseURL.appendingPathComponent("api/v1/audio/asr")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let boundary = "Boundary-\(UUID().uuidString)"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = makeMultipartBody(
            boundary: boundary,
            fieldName: "file",
            filename: "speech.pcm",
            mime: "audio/pcm",
            data: data
        )

        let (responseData, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw AudioServiceError.invalidResponse }
        guard http.statusCode == 200 else {
            let detail = (try? JSONDecoder().decode(SimpleErrorBody.self, from: responseData))?.detail
                ?? "语音识别失败"
            throw AudioServiceError.server(status: http.statusCode, message: detail)
        }
        let parsed = try JSONDecoder().decode(ASRResponse.self, from: responseData)
        return parsed.text
    }

    public func synthesize(text: String, voice: SpeechVoice) async throws -> Data {
        let url = baseURL.appendingPathComponent("api/v1/audio/tts")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(TTSRequest(text: text, voice: voice.id))

        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw AudioServiceError.invalidResponse }
        guard http.statusCode == 200 else {
            let detail = (try? JSONDecoder().decode(SimpleErrorBody.self, from: data))?.detail
                ?? "语音合成失败"
            throw AudioServiceError.server(status: http.statusCode, message: detail)
        }
        return data
    }

    private func makeMultipartBody(
        boundary: String,
        fieldName: String,
        filename: String,
        mime: String,
        data: Data
    ) -> Data {
        var body = Data()
        let lineBreak = "\r\n"
        func append(_ value: String) {
            body.append(value.data(using: .utf8)!)
        }
        append("--\(boundary)\(lineBreak)")
        append("Content-Disposition: form-data; name=\"\(fieldName)\"; filename=\"\(filename)\"\(lineBreak)")
        append("Content-Type: \(mime)\(lineBreak)\(lineBreak)")
        body.append(data)
        append(lineBreak)
        append("--\(boundary)--\(lineBreak)")
        return body
    }
}

public enum AudioServiceError: Error, LocalizedError, Equatable {
    case invalidResponse
    case server(status: Int, message: String)

    public var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "语音服务返回异常"
        case .server(_, let message):
            return message
        }
    }
}

private struct ASRResponse: Decodable {
    let text: String
}

private struct TTSRequest: Encodable {
    let text: String
    let voice: String
}

private struct SimpleErrorBody: Decodable {
    let detail: String?
}
