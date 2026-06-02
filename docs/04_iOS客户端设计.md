# 04 · iOS 客户端设计

> 配套主文档 `01_项目开发文档.md`。本篇聚焦 Swift / SwiftUI 客户端的模块划分、SSE 流式渲染、商品卡片组件、购物车视图、以及多模态采集（语音、相机）。
> 后端契约见 `03_后端API与Agent编排.md`，本文档不再重复 API 字段。

---

## 1. 客户端整体定位

- **必须原生**（课题强制），用 **Swift 5.9 + SwiftUI**，目标 iOS 16+
- 单 App 多 Scene 在本课题用不上，**单窗口** + Tab 即可
- 不引入跨平台框架（Flutter / RN / 任何 WebView 套壳）

---

## 2. 项目结构（Xcode）

```
client/ShoppingGuide.xcodeproj
client/ShoppingGuide/
├── App/
│   ├── ShoppingGuideApp.swift           # @main
│   └── AppEnvironment.swift             # 全局 ObservableObject：BaseURL / sessionId
├── Features/
│   ├── Chat/
│   │   ├── ChatView.swift               # 聊天主页面
│   │   ├── ChatViewModel.swift          # 业务逻辑 + SSE 消费
│   │   ├── MessageBubble.swift          # 单条气泡
│   │   ├── StreamingClient.swift        # SSE 长连接
│   │   └── ClarifyChipsView.swift       # 主动澄清选项
│   ├── Product/
│   │   ├── ProductCardView.swift        # 嵌入对话流的卡片
│   │   ├── ProductDetailView.swift      # 详情页（push 进入）
│   │   └── ProductModel.swift           # Codable
│   ├── Cart/
│   │   ├── CartView.swift               # 购物车 Tab
│   │   ├── CartViewModel.swift
│   │   └── CartRow.swift
│   └── Multimodal/
│       ├── VoiceInputView.swift         # 按住说话
│       ├── CameraPickerView.swift       # 相机/相册
│       └── AudioRecorder.swift
├── Networking/
│   ├── APIClient.swift                  # URLSession 封装
│   ├── SSEParser.swift                  # 流式事件解析
│   └── Endpoints.swift                  # 路径常量
├── Models/
│   ├── ChatMessage.swift                # 消息领域模型
│   ├── SSEEvent.swift                   # 事件类型枚举
│   └── ServerError.swift
├── Components/
│   ├── ShimmerView.swift                # 骨架屏（加分项）
│   └── StreamingTextRenderer.swift      # 增量富文本
└── Resources/
    └── Assets.xcassets
```

**分层原则**：`Features/*` 只负责视图和绑定，业务流转写在对应 `*ViewModel`；`Networking/*` 只处理 HTTP/SSE。

---

## 3. 数据模型

### 3.1 `ChatMessage`（视图层模型）

```swift
struct ChatMessage: Identifiable, Equatable {
    let id: UUID
    enum Role { case user, assistant, system }
    let role: Role

    // 流式增量拼接到 text；卡片单独存
    var text: String
    var productCards: [ProductCard]
    var isStreaming: Bool          // true 时显示光标动画

    // 主动澄清
    var clarify: ClarifyPayload?

    // 工具调用反馈（购物车加成功的小提示气泡）
    var toolNotice: String?

    let createdAt: Date
}
```

### 3.2 `ProductCard`

```swift
struct ProductCard: Identifiable, Codable, Equatable {
    var id: String { productId }
    let productId: String
    let title: String
    let brand: String
    let category: String
    let imageURL: URL
    let priceRange: PriceRange
    let skus: [SKU]
    let reason: String

    struct PriceRange: Codable, Equatable {
        let min: Double
        let max: Double
    }
    struct SKU: Codable, Equatable, Identifiable {
        var id: String { skuId }
        let skuId: String
        let properties: [String: String]
        let price: Double
    }
}
```

> 用 `JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase`，对齐后端 snake_case。

### 3.3 `SSEEvent`

```swift
enum SSEEvent {
    case session(String)
    case status(stage: String)
    case token(String)
    case productCard(ProductCard)
    case clarify(ClarifyPayload)
    case toolResult(ToolResult)
    case cartUpdate(CartSnapshot)
    case error(code: String, message: String)
    case done
}
```

---

## 4. SSE 流式实现（**核心难点**）

iOS 没有官方 SSE 库，但 `URLSession + URLSessionDataDelegate` 可以稳定实现。**绝不要**用 `dataTask(completionHandler:)`——那是一次性返回。

### 4.1 `StreamingClient.swift` 骨架

```swift
final class StreamingClient: NSObject, URLSessionDataDelegate {
    private var session: URLSession!
    private var task: URLSessionDataTask?
    private var buffer = Data()
    private var continuation: AsyncStream<SSEEvent>.Continuation?

    override init() {
        super.init()
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 60       // SSE 长连接
        cfg.timeoutIntervalForResource = 120
        session = URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
    }

    /// 发起流式对话
    func stream(_ request: URLRequest) -> AsyncStream<SSEEvent> {
        AsyncStream { continuation in
            self.continuation = continuation
            self.task = session.dataTask(with: request)
            self.task?.resume()
            continuation.onTermination = { @Sendable _ in
                self.task?.cancel()
            }
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        // SSE 以 \n\n 分隔事件
        while let range = buffer.range(of: Data("\n\n".utf8)) {
            let block = buffer.subdata(in: 0..<range.lowerBound)
            buffer.removeSubrange(0..<range.upperBound)
            if let evt = SSEParser.parse(block) {
                continuation?.yield(evt)
                if case .done = evt {
                    continuation?.finish()
                    return
                }
            }
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let err = error, (err as NSError).code != NSURLErrorCancelled {
            continuation?.yield(.error(code: "NETWORK", message: err.localizedDescription))
        }
        continuation?.finish()
    }
}
```

### 4.2 `SSEParser.swift`

```swift
enum SSEParser {
    static func parse(_ block: Data) -> SSEEvent? {
        guard let text = String(data: block, encoding: .utf8) else { return nil }
        var event = "message"
        var dataLines: [String] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.hasPrefix(":") { continue }   // 心跳注释
            if line.hasPrefix("event:") {
                event = String(line.dropFirst("event:".count)).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                dataLines.append(String(line.dropFirst("data:".count)).trimmingCharacters(in: .whitespaces))
            }
        }
        let jsonString = dataLines.joined(separator: "\n")
        let jsonData = Data(jsonString.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        switch event {
        case "session":
            let p = try? decoder.decode([String: String].self, from: jsonData)
            return p?["sessionId"].map(SSEEvent.session)
        case "status":
            let p = try? decoder.decode([String: String].self, from: jsonData)
            return p?["stage"].map(SSEEvent.status)
        case "token":
            let p = try? decoder.decode([String: String].self, from: jsonData)
            return p?["text"].map(SSEEvent.token)
        case "product_card":
            return (try? decoder.decode(ProductCard.self, from: jsonData)).map(SSEEvent.productCard)
        case "clarify":
            return (try? decoder.decode(ClarifyPayload.self, from: jsonData)).map(SSEEvent.clarify)
        case "tool_result":
            return (try? decoder.decode(ToolResult.self, from: jsonData)).map(SSEEvent.toolResult)
        case "cart_update":
            return (try? decoder.decode(CartSnapshot.self, from: jsonData)).map(SSEEvent.cartUpdate)
        case "error":
            let p = try? decoder.decode([String: String].self, from: jsonData)
            return .error(code: p?["code"] ?? "UNKNOWN", message: p?["message"] ?? "")
        case "done":
            return .done
        default:
            return nil
        }
    }
}
```

### 4.3 `ChatViewModel` 消费

```swift
@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var inputText: String = ""
    @Published var isSending: Bool = false
    @Published var cart: CartSnapshot = .empty

    private let client = StreamingClient()
    private let api = APIClient.shared

    func send() async {
        let userMsg = ChatMessage(id: UUID(), role: .user, text: inputText, productCards: [], isStreaming: false, clarify: nil, toolNotice: nil, createdAt: .now)
        messages.append(userMsg)

        var assistantMsg = ChatMessage(id: UUID(), role: .assistant, text: "", productCards: [], isStreaming: true, clarify: nil, toolNotice: nil, createdAt: .now)
        messages.append(assistantMsg)
        let assistantIdx = messages.count - 1

        let req = api.buildChatStreamRequest(message: inputText, sessionId: AppEnvironment.shared.sessionId)
        inputText = ""
        isSending = true

        for await event in client.stream(req) {
            switch event {
            case .session(let id):
                AppEnvironment.shared.sessionId = id
            case .token(let t):
                messages[assistantIdx].text += t
            case .productCard(let card):
                messages[assistantIdx].productCards.append(card)
            case .clarify(let p):
                messages[assistantIdx].clarify = p
            case .toolResult(let r):
                messages[assistantIdx].toolNotice = r.displayText
            case .cartUpdate(let snap):
                self.cart = snap
            case .error(_, let msg):
                messages[assistantIdx].text += "\n[出错了：\(msg)]"
            case .done:
                messages[assistantIdx].isStreaming = false
            case .status: break
            }
        }
        isSending = false
    }
}
```

### 4.4 流式渲染体验

- 用 `messages[assistantIdx].text += t` 直接驱动 SwiftUI 重渲染，**不要**自己写定时器一字一字蹦
- 在 `MessageBubble` 末尾对 `isStreaming == true` 的消息追加一个闪烁的"▋"光标，参考豆包效果
- 自动滚到底：用 `ScrollViewReader` + `proxy.scrollTo(lastMessageId, anchor: .bottom)`，且监听 `messages.last?.text` 变化

---

## 5. UI 设计

### 5.1 主框架（Tab）

```swift
TabView {
    ChatView()
        .tabItem { Label("导购", systemImage: "bubble.left.and.bubble.right") }
    CartView()
        .tabItem { Label("购物车", systemImage: "cart")
                   .badge(cart.totalCount) }
    DiscoverView()    // 商品列表，调试 & 备用入口
        .tabItem { Label("发现", systemImage: "square.grid.2x2") }
}
```

### 5.2 `ChatView` 布局

```
┌─────────────────────────────────┐
│ 顶部：会话标题 + 新建会话按钮    │
├─────────────────────────────────┤
│                                 │
│  消息列表（ScrollView）          │
│  - 用户气泡 右对齐 蓝色          │
│  - Assistant 气泡 左对齐 灰色    │
│    末尾可能跟一组商品卡片         │
│    或一组 Clarify chips          │
│                                 │
├─────────────────────────────────┤
│ [🎤] [📷] 输入框…………… [发送] │
└─────────────────────────────────┘
```

### 5.3 `ProductCardView` 设计

横向卡片，宽度自适应消息气泡：

```
┌───────────────────────────────┐
│  [80x80 图]  品牌 · 类目        │
│              标题（最多 2 行）   │
│              ¥720 ~ ¥1260       │
│              ┌─────────────┐    │
│              │ 推荐理由…    │    │
│              └─────────────┘    │
│              [加入购物车]        │
└───────────────────────────────┘
点击整个卡片 → push 进 ProductDetailView
点击「加入购物车」按钮 → 触发对话：
  发送 "把刚才的雅诗兰黛加到购物车" 给后端
  让 Agent 走 tool_use 路径（保持对话闭环一致）
```

> 不直接调 `/cart` POST，因为课题考察的是"对话式 CRUD"。"加入购物车"按钮是发起一句话，**经过 Agent**。这样购物车更新也会有自然语言确认。

### 5.4 Clarify Chips

```swift
HStack {
    ForEach(clarify.options, id: \.self) { opt in
        Button(opt) { Task { viewModel.inputText = opt; await viewModel.send() } }
            .buttonStyle(.bordered)
    }
}
```

---

## 6. 多模态采集

### 6.1 语音输入（加分项 4.2 ⭐）

`VoiceInputView`：点击麦克风开始录音，再次点击停止；停止后上传服务端 ASR，转写文本写入输入框，由用户确认发送。

- 用 `AVAudioEngine` + `AVAudioConverter` 录 16kHz / 16-bit / mono raw PCM
- 主链路：`POST /api/v1/audio/asr`，后端通过豆包语音 OpenSpeech ASR `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel` 转写
- 降级链路：如果远端录音/ASR 不可用，保留 `SFSpeechRecognizer(locale: zh-CN)`
- 转文字后只写入 `inputText`，**不自动发送**，避免识别误差直接触发错误请求

### 6.2 相机 / 相册（加分项 4.2 ⭐⭐⭐）

`CameraPickerView` 用 `PhotosPicker`（iOS 16+）：

```swift
PhotosPicker(selection: $selectedItem, matching: .images) { Image(systemName: "camera") }
    .onChange(of: selectedItem) { _, item in
        Task {
            guard let data = try? await item?.loadTransferable(type: Data.self) else { return }
            let compressed = ImageUtil.compress(data, maxKB: 1024)
            let imageId = try await APIClient.shared.uploadImage(compressed)
            await viewModel.send(message: "找一下同款", imageId: imageId)
        }
    }
```

> `send(message:imageId:)` 把 imageId 写进 ChatRequest body 即可。

### 6.3 TTS（加分项 4.2 ⭐⭐）

assistant 非 streaming 回复下方显示 speaker 按钮；Header 提供自动播报开关和音色菜单。

```swift
struct SpeechVoice: Identifiable {
    let id: String
    let displayName: String
    let locale: String
}

func speak(_ text: String, voice: SpeechVoice) async {
    let wav = try await audioService.synthesize(text: text, voice: voice)
    let player = try AVAudioPlayer(data: wav)
    player.play()
}
```

- 主链路：`POST /api/v1/audio/tts`，后端通过豆包语音 OpenSpeech TTS `wss://openspeech.bytedance.com/api/v3/tts/bidirection` 合成
- 返回：`audio/wav`，iOS 用 `AVAudioPlayer` 播放
- 失败处理：远端 TTS 失败时停止本次播报，不走 `AVSpeechSynthesizer` 原生朗读
- 音色：Header 菜单从 10 个内置 voice id 中选择，`ChatViewModel.selectedVoice` 透传给 TTS 请求

---

## 7. 购物车视图

- `CartView` 进入时 `GET /api/v1/cart?session_id=...` 拉取
- 同时订阅 ChatViewModel 的 `cart` Published（聊天页改了立刻同步）
- 每行：商品图 + 标题 + SKU 描述 + 数量步进器 + 删除按钮
- 删除走 **对话路径**：触发 ChatView 发送"把第 X 个删掉"——保证 Agent 上下文同步
- 底部"去结算"→ 触发对话"下单吧，地址用默认的"，演示加分项

---

## 8. 网络与配置

### 8.1 `APIClient`

```swift
final class APIClient {
    static let shared = APIClient(baseURL: AppEnvironment.shared.baseURL)
    private let baseURL: URL
    private let session: URLSession

    init(baseURL: URL) {
        self.baseURL = baseURL
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: cfg)
    }

    func buildChatStreamRequest(message: String, sessionId: String?, imageId: String? = nil) -> URLRequest {
        var req = URLRequest(url: baseURL.appendingPathComponent("api/v1/chat/stream"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        let body: [String: Any?] = [
            "session_id": sessionId,
            "message": message,
            "image_id": imageId
        ]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body.compactMapValues { $0 })
        return req
    }
}
```

### 8.2 BaseURL 切换

`AppEnvironment.swift`：
```swift
final class AppEnvironment: ObservableObject {
    static let shared = AppEnvironment()
    @Published var baseURL: URL = URL(string: "http://localhost:8000")!
    @Published var sessionId: String? = nil
}
```

Debug 菜单允许切换"localhost / 局域网 IP / 远程"。真机调试时填 Mac 的局域网 IP。

### 8.3 ATS（明文 HTTP 调试）

`Info.plist` 加（仅开发期）：
```xml
<key>NSAppTransportSecurity</key>
<dict>
    <key>NSAllowsArbitraryLoads</key><true/>
</dict>
```
发布前必须收紧。

---

## 9. 体验打磨（加分项 4.4 ⭐⭐⭐）

| 打磨点 | 做法 |
| --- | --- |
| 商品卡片加载占位 | `ShimmerView` 在 image 加载完成前显示 |
| 进入页面动画 | `.transition(.opacity.combined(with: .move(edge: .bottom)))` |
| 卡片点击反馈 | `.scaleEffect(isPressed ? 0.97 : 1)` |
| 收藏动画 | "+1" 飞向 Cart Tab 的 badge |
| 输入框聚焦 | 自动滚动消息列表到底 |
| Haptic | 卡片插入时 `UIImpactFeedbackGenerator(.soft).impactOccurred()` |
| 暗色模式 | SwiftUI 默认跟随系统，验收时记得切到暗色看一遍 |

---

## 10. 依赖（Swift Package Manager）

`File → Add Packages…` 加入：

- `https://github.com/gonzalezreal/swift-markdown-ui` —— MarkdownUI 2.3+（assistant 富文本）
- `https://github.com/onevcat/Kingfisher` —— 7.11+（远程图片缓存）

其余系统框架：SwiftUI / Combine / URLSession / AVFoundation / Speech / PhotosUI。

---

## 11. 真机调试要点

1. Mac 后端跑 `uvicorn --host 0.0.0.0 --port 8000`
2. `ifconfig | grep inet` 查 Mac 局域网 IP（如 192.168.1.5）
3. iPhone 在 Debug 菜单里把 BaseURL 改成 `http://192.168.1.5:8000`
4. 手机与 Mac **同一 Wi-Fi**
5. 防火墙允许 8000 端口入站
6. 首次连接如果 SSL 报错，临时打开 ATS（见 §8.3）

---

## 12. 客户端验收清单

- [ ] 首次启动能成功握手 `/chat/sessions`（或随对话自动创建）
- [ ] 文本输入 → 逐字流式回复 → 末尾出现商品卡片
- [ ] 商品卡片点击进详情，详情页字段齐全
- [ ] 卡片"加入购物车"按钮触发对话，购物车 Tab badge 同步更新
- [ ] 购物车 Tab 数据与对话内一致
- [ ] 至少 1 个多模态入口可用（语音 / 拍照）
- [ ] 网络断开时显示友好错误，不闪退
- [ ] 暗色模式 / Dynamic Type 字体放大后布局不破

---

## 13. 提交规范

- Xcode 项目根目录 `client/`
- 不要提交 `Pods/` `*.xcuserstate` `xcuserdata/`
- README 包含：最低 iOS 版本、最低 Xcode 版本、运行步骤、BaseURL 修改位置

---

至此 4 篇文档闭环。开发顺序建议：

```
01 主文档 → 全员看
02 数据/RAG → 后端先建索引
03 后端 API → 后端把 SSE 接口跑起来 → 给 iOS 联调
04 iOS → iOS 端串通流式 → 联调 → 加分项分头做
```
