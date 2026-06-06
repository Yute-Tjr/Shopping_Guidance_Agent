# iOS 客户端 README

> 客户端是 PriceCat 的原生 iOS App，使用 SwiftUI 构建聊天、商品卡片、详情页、图片上传、语音输入和 TTS 播放体验。工程入口是 `client/ShoppingGuide.xcodeproj`。

## 1. 功能范围

- 流式聊天：消费后端 `POST /api/v1/chat/stream` SSE 事件，逐字渲染回答。
- 商品卡片：展示后端返回的真实商品图、品牌、价格区间、SKU 和推荐理由。
- 商品详情：点击卡片后请求 `GET /api/v1/products/{product_id}`。
- 主动澄清：渲染后端 `clarify` 事件返回的问题和选项。
- 图片找货：选择图片后上传到 `POST /api/v1/upload/image`，再带 `image_id` 进入对话。
- 语音输入：录音并上传 PCM 到 `POST /api/v1/audio/asr`，将识别文本回填输入框。
- 语音播报：调用 `POST /api/v1/audio/tts` 获取 WAV 并播放，支持音色切换和自动播报。

## 2. 环境要求

- macOS 14+
- Xcode 15+，推荐 Xcode 16+
- iOS 16+ 模拟器或真机
- Swift 5.9
- 后端 API 可访问：公网沙箱或本地 `http://127.0.0.1:8000`

语音相关权限已在 `ShoppingGuide-Info.plist` 配置：

- `NSMicrophoneUsageDescription`
- `NSSpeechRecognitionUsageDescription`

## 3. 目录结构

```text
client/
├── README.md
├── Package.swift                         # macOS 逻辑单测用 SwiftPM 包
├── ShoppingGuide-Info.plist
├── ShoppingGuide.xcodeproj
├── ShoppingGuide/
│   ├── App/
│   │   ├── ShoppingGuideApp.swift        # @main
│   │   ├── AppEnvironment.swift          # BaseURL、sessionID
│   │   └── LaunchIntroView.swift         # 开场动画
│   ├── Components/
│   │   ├── MarkdownParser.swift
│   │   └── MarkdownView.swift
│   ├── Features/
│   │   ├── Chat/
│   │   │   ├── ChatView.swift
│   │   │   ├── ChatViewModel.swift
│   │   │   ├── ChatTransport.swift
│   │   │   ├── ImagePicker.swift
│   │   │   ├── MessageBubble.swift
│   │   │   ├── SpeechRecognitionService.swift
│   │   │   └── SpeechSynthesisService.swift
│   │   └── Product/
│   │       ├── ProductCardView.swift
│   │       └── ProductDetailView.swift
│   ├── Models/
│   ├── Networking/
│   │   ├── APIClient.swift
│   │   ├── AudioService.swift
│   │   ├── Endpoints.swift
│   │   ├── SSEParser.swift
│   │   ├── StreamingClient.swift
│   │   └── UploadService.swift
│   └── Resources/
│       ├── Assets.xcassets
│       ├── Theme.swift
│       └── app_begin.mp4
└── Tests/ShoppingGuideKitTests/
```

## 4. 后端地址配置

当前默认地址在 `ShoppingGuide/App/AppEnvironment.swift`：

```swift
@Published var baseURL: URL = URL(string: "http://121.196.247.225")!
```

这适合直接连公网沙箱体验。如果要连接本地后端，改成：

```swift
@Published var baseURL: URL = URL(string: "http://127.0.0.1:8000")!
```

真机连接本机后端时，`127.0.0.1` 指向手机自身，不能访问 Mac。请改成 Mac 在同一局域网下的 IP，例如：

```swift
@Published var baseURL: URL = URL(string: "http://192.168.1.23:8000")!
```

本项目为了 Demo 方便仍使用 HTTP。若切换到 HTTPS 域名，需要同步检查 ATS 配置和后端 CORS。

## 5. 快速体验

### 5.1 使用公网沙箱

```bash
cd client
open ShoppingGuide.xcodeproj
```

在 Xcode 里选择 iOS 16+ 模拟器或真机，Cmd+R 运行。可尝试：

- `推荐一款适合油皮的洗面奶`
- `200 元以下的蓝牙耳机`
- `不要兰蔻，推荐一款精华`
- `这两款帮我用表格对比一下`

公网沙箱依赖服务器和模型密钥可用性；如果请求失败，切换到本地后端。

### 5.2 使用本地后端

先按 [../server/README.md](../server/README.md) 启动 API，并确认：

```bash
curl http://127.0.0.1:8000/healthz
```

然后把 `AppEnvironment.swift` 的 `baseURL` 改为本地地址，重新运行 App。

## 6. 主要体验路径

### 文本推荐

1. 输入商品需求。
2. 观察 assistant 气泡逐字出现。
3. 等待商品卡片出现。
4. 点击卡片进入详情页。

### 图片找货

1. 点击输入栏图片按钮。
2. 从相册选择商品图或场景图。
3. 等待上传完成后补充文字，例如 `帮我找相似但便宜一点的`。
4. 发送后查看多模态召回结果。

### 语音输入和播报

1. 点击麦克风按钮，首次运行时允许麦克风权限。
2. 再次点击停止录音。
3. ASR 结果会回填到输入框，可编辑后发送。
4. assistant 回复完成后，点击气泡内 speaker 按钮播放 TTS。
5. Header 中可切换音色，也可开启自动播报。

## 7. 命令行构建和测试

客户端 UI 由 Xcode 工程构建，逻辑层单测通过同级 `Package.swift` 运行：

```bash
cd client
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

可覆盖的核心逻辑包括：

- SSE 帧解析和 `\r\n` 兼容。
- ChatViewModel 的流式消息拼接、商品卡片处理、主动澄清。
- MarkdownParser。
- 商品卡片选择和导航状态。
- 开场动画状态。

如果要用命令行跑模拟器构建：

```bash
cd client
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcodebuild -project ShoppingGuide.xcodeproj \
  -scheme ShoppingGuide \
  -destination "platform=iOS Simulator,name=iPhone 17" \
  -configuration Debug build
```

## 8. 常见问题

- **App 一直转圈或网络失败**：先确认 `baseURL` 是否能从模拟器或真机访问，再访问 `/healthz`。
- **真机连不上本地后端**：不要用 `127.0.0.1`，改用 Mac 局域网 IP，并确保防火墙允许 8000 端口。
- **商品图不显示**：后端部署时 `STATIC_BASE_URL` 可能没配置，或 Nginx 没暴露 `/static`。
- **语音按钮不可用**：检查麦克风权限；模拟器音频链路不稳定时建议用真机验收。
- **TTS 没声音**：确认后端 `/api/v1/audio/voices` 和 `/api/v1/audio/tts` 可用，且设备未静音。
- **SSE 文本不连续**：检查后端是否真的返回 `event: token`，不要把 `product_card` 或 `done` 当文本拼接。

## 9. 与后端契约

客户端路径常量集中在 `ShoppingGuide/Networking/Endpoints.swift`：

```swift
public enum Endpoints {
    public static let chatStream = "api/v1/chat/stream"
    public static func productDetail(_ productId: String) -> String {
        "api/v1/products/\(productId)"
    }
    public static let health = "healthz"
}
```

服务端返回 snake_case，客户端模型使用 `JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase` 解码。事件层如果先解成字典，需要直接读取后端原始键名，例如 `session_id`。
