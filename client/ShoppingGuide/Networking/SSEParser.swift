import Foundation

/// 把一帧 SSE 报文（不含尾随空行）解析成 `SSEEvent`。
///
/// SSE 帧结构（W3C spec）：
/// - 多行，行间 `\n`，帧间 `\n\n`
/// - `event: <name>` 指定事件类型，默认 `message`
/// - `data: <payload>` 可重复多行，最终需 `\n` 拼回
/// - 以 `:` 开头的行为注释（心跳），整行忽略
///
/// 与后端 `app/api/chat.py` 的事件契约一一对应：
/// `session / status / token / product_card / clarify / error / done`。
/// 其它事件类型与 JSON 解码失败均返回 nil（StreamingClient 会跳过）。
public enum SSEParser {

    public static func parse(_ block: Data) -> SSEEvent? {
        guard let text = String(data: block, encoding: .utf8) else { return nil }

        var eventName = "message"
        var dataLines: [String] = []

        // **重要**：Swift 把 \r\n 当成一个 grapheme cluster，直接 split(separator:"\n")
        // 会一行都拆不开（sse-starlette 偏偏用 \r\n 当行尾）。先归一化所有换行风格再切。
        let normalized = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")

        for raw in normalized.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw)
            if line.isEmpty { continue }
            if line.hasPrefix(":") { continue }  // 心跳注释
            if let value = stripPrefix(line, "event:") {
                eventName = value
            } else if let value = stripPrefix(line, "data:") {
                dataLines.append(value)
            }
        }

        let payload = dataLines.joined(separator: "\n")
        return decode(eventName: eventName, payload: payload)
    }

    // MARK: - Helpers

    private static func stripPrefix(_ s: String, _ prefix: String) -> String? {
        guard s.hasPrefix(prefix) else { return nil }
        let v = s.dropFirst(prefix.count)
        return String(v).trimmingCharacters(in: .whitespaces)
    }

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    private static func decode(eventName: String, payload: String) -> SSEEvent? {
        let data = Data(payload.utf8)
        switch eventName {
        case "session":
            // JSONDecoder 的 convertFromSnakeCase 不作用于 [String:String]，
            // 这里直接读后端原始 snake_case 键。
            let p = try? decoder.decode([String: String].self, from: data)
            return p?["session_id"].map { SSEEvent.session(id: $0) }
        case "status":
            let p = try? decoder.decode([String: String].self, from: data)
            return p?["stage"].map { SSEEvent.status(stage: $0) }
        case "token":
            let p = try? decoder.decode([String: String].self, from: data)
            return p?["text"].map { SSEEvent.token(text: $0) }
        case "product_card":
            guard let card = try? decoder.decode(ProductCard.self, from: data) else { return nil }
            return .productCard(card)
        case "clarify":
            guard let p = try? decoder.decode(ClarifyPayload.self, from: data) else { return nil }
            return .clarify(p)
        case "error":
            let p = try? decoder.decode([String: String].self, from: data)
            return .error(code: p?["code"] ?? "UNKNOWN", message: p?["message"] ?? "")
        case "done":
            return .done
        default:
            return nil
        }
    }
}
