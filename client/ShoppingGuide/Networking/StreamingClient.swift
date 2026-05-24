import Foundation

/// SSE 长连接客户端。
///
/// 关键设计：
/// - 用 `URLSessionDataDelegate` 而非 `dataTask(completionHandler:)`，后者
///   只在 body 接收完毕才回调，SSE 必须边收边解；
/// - 缓冲区按 `\n\n` 切帧，剩下的不完整帧留在 buffer；
/// - 解析出 `.done` 立刻 `finish()`，否则等 `didCompleteWithError` 或客户端
///   取消时再 finish；
/// - `Sendable` 友好：实例每次只服务一条流，状态不跨线程共享。
public final class StreamingClient: NSObject, URLSessionDataDelegate {

    private var session: URLSession!
    private var task: URLSessionDataTask?
    private var buffer = Data()
    private var continuation: AsyncStream<SSEEvent>.Continuation?

    public override init() {
        super.init()
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 60      // SSE 单次请求允许长存活
        cfg.timeoutIntervalForResource = 120
        cfg.httpAdditionalHeaders = ["Accept": "text/event-stream"]
        session = URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
    }

    /// 发起请求并返回 SSE 事件流。调用方 `for await evt in client.stream(req) { ... }` 消费。
    /// 流取消（被上层 break）时自动 cancel underlying task。
    ///
    /// 注意：闭包里**强引用 self**，让 StreamingClient 至少活到 AsyncStream 结束——
    /// 否则在 `LiveChatTransport.stream` 这种"创建即返回"的调用方里实例会被立即释放，
    /// URLSession 跟着失效，delegate 回调永远不来。
    public func stream(_ request: URLRequest) -> AsyncStream<SSEEvent> {
        AsyncStream { continuation in
            self.continuation = continuation
            self.buffer.removeAll(keepingCapacity: true)
            self.task = self.session.dataTask(with: request)
            self.task?.resume()
            continuation.onTermination = { _ in
                // 显式捕获 self（不用 weak）：保证 task.cancel() 能跑到，
                // 同时延长生命周期到流结束
                self.task?.cancel()
            }
        }
    }

    // MARK: - URLSessionDataDelegate

    public func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        // SSE 规范允许换行用 \r\n / \n / \r；sse-starlette 实际用 \r\n\r\n 分帧。
        // 这里两种分隔符都试，谁先出现先切谁。
        let crlfSep = Data("\r\n\r\n".utf8)
        let lfSep = Data("\n\n".utf8)
        while true {
            let crlfRange = buffer.range(of: crlfSep)
            let lfRange = buffer.range(of: lfSep)
            let pick: (Range<Data.Index>, Int)? = {
                switch (crlfRange, lfRange) {
                case let (c?, l?): return c.lowerBound <= l.lowerBound ? (c, 4) : (l, 2)
                case let (c?, nil): return (c, 4)
                case let (nil, l?): return (l, 2)
                case (nil, nil): return nil
                }
            }()
            guard let (range, sepLen) = pick else { break }
            let block = buffer.subdata(in: 0..<range.lowerBound)
            _ = sepLen   // 保留语义；移除 separator 用 range.upperBound 已足够
            buffer.removeSubrange(0..<range.upperBound)
            guard !block.isEmpty else { continue }
            if let evt = SSEParser.parse(block) {
                continuation?.yield(evt)
                if case .done = evt {
                    continuation?.finish()
                    return
                }
            }
            // 解析失败的帧（未知 event / 坏 JSON）直接静默丢弃
        }
    }

    public func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let err = error, (err as NSError).code != NSURLErrorCancelled {
            continuation?.yield(.error(code: "NETWORK", message: err.localizedDescription))
        }
        continuation?.finish()
    }
}
