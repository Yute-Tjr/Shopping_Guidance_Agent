import Foundation

/// `event: clarify` 的 payload；后端在意图判断为 clarify_needed 时下发。
public struct ClarifyPayload: Codable, Equatable, Sendable {
    public let question: String
    public let options: [String]

    public init(question: String, options: [String]) {
        self.question = question
        self.options = options
    }
}
