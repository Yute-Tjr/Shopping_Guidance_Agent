# iOS 客户端 · 启动说明

## 环境要求

- Xcode **15+**（实测 Xcode 16 / objectVersion 77 也 OK）
- macOS 14+
- iOS 部署目标 **16.0+**
- Swift **5.9**
- 语音输入 / 播报需要模拟器或真机允许麦克风权限；工程已配置 `NSMicrophoneUsageDescription` / `NSSpeechRecognitionUsageDescription`

## Phase 0 验收：跑通空白页

当前仓库已提交 `ShoppingGuide.xcodeproj`，通常直接打开即可。下面的 Phase 0
步骤保留给需要在本机重建 `.xcodeproj` 的场景。

### 关键约束（先看，避免踩坑）

最终要达成的布局是 **`.xcodeproj` 与源码包 `ShoppingGuide/` 平级**，都在
`client/` 目录下：

```
client/
├── ShoppingGuide.xcodeproj          ← 工程文件（本地生成，进 git）
└── ShoppingGuide/                   ← 源码包（已在仓库里）
    ├── App/  Features/  Networking/  Models/  Components/  Resources/
```

两个会让你卡住的点：

1. **不能把 Xcode 工程的存储位置直接选成 `client/`** —— 仓库里
   `client/ShoppingGuide/` 已经存在，Xcode 会报 `directory already exists`。
   解决：先生成到一个临时目录，再把 `.xcodeproj` 单独挪过来（步骤 2-3）。
2. **新建工程时务必不勾 "Create Git repository on my Mac"** —— 会在
   `client/ShoppingGuide/` 下生成嵌套子仓库 `.git/`，外层仓库会把它当
   submodule。

> **Xcode 16 用户福利**：Xcode 16 默认用 `PBXFileSystemSynchronizedRootGroup`
> （同步文件夹组），`.xcodeproj` 一旦放到 `client/` 下，会自动把同级的
> `ShoppingGuide/` 整目录纳入 target，**完全不需要 Add Files**。下面的步骤 4
> 是给 Xcode 15 用户用的，Xcode 16 用户可以跳过。

### 步骤 1 · 确认仓库骨架已就位

```bash
$ ls client/
README.md   ShoppingGuide/   Tests/
$ ls client/ShoppingGuide/
App  Components  Features  Models  Networking  Resources
```

如果 `client/` 下出现 `tmp/`、`ShoppingGuide.backup/` 或者
`ShoppingGuide/.git/`，说明上次准备失败留了残留，先清理掉再开始。

### 步骤 2 · 在临时目录里新建 Xcode 工程

1. 打开 Xcode → `File → New → Project…`
2. 选择 **iOS → App** 模板，Next。
3. 填表：
   - Product Name：`ShoppingGuide`（**必须与仓库目录同名，大小写一致**）
   - Team：随意（个人 Apple ID 即可）
   - Organization Identifier：`com.<yourname>`（任意反向域名）
   - Interface：`SwiftUI`
   - Language：`Swift`
   - Storage：`None`
   - Include Tests：可选
4. Next，存储位置对话框：
   - **位置选一个临时目录**，如 `~/Desktop/xcode-tmp/`
   - **取消勾选** "Create Git repository on my Mac"
5. Create。Xcode 会生成：
   ```
   ~/Desktop/xcode-tmp/ShoppingGuide/
   ├── ShoppingGuide.xcodeproj    ← 等会挪走
   └── ShoppingGuide/             ← Xcode 默认源码，整目录丢弃
       ├── ShoppingGuideApp.swift
       ├── ContentView.swift
       └── Assets.xcassets/
   ```
6. **关闭 Xcode 工程窗口**（Cmd+W），避免下一步移动文件时 Xcode 缓存旧路径。

### 步骤 3 · 把 `.xcodeproj` 挪到 `client/`，丢弃其余

终端执行（`<repo>` 替成本仓库路径）：

```bash
mv ~/Desktop/xcode-tmp/ShoppingGuide/ShoppingGuide.xcodeproj \
   "<repo>/client/ShoppingGuide.xcodeproj"

rm -rf ~/Desktop/xcode-tmp
```

此时 `client/` 目录应该是：

```
client/
├── README.md
├── ShoppingGuide.xcodeproj    ← 刚挪过来
├── ShoppingGuide/             ← 仓库里的源码骨架
└── Tests/
```

双击 `ShoppingGuide.xcodeproj` 打开。

### 步骤 4 · 让工程指向仓库源码

**Xcode 16+（推荐路径）**：

Xcode 16 的工程文件用同步文件夹组，`.xcodeproj` 里以相对路径 `ShoppingGuide`
引用源码目录，挪过来之后这个相对路径正好命中我们的 `client/ShoppingGuide/`。
打开工程时 Project Navigator 左侧应自动显示 `App / Components / Features /
Models / Networking / Resources` 六个目录。**什么都不用做，直接跳到步骤 5**。

**Xcode 15（手工挂载）**：

1. Project Navigator 里默认的 `ShoppingGuide` 组应当是空的（源码已随临时目录
   丢弃）。如果还残留指向临时目录的红色文件名，全部 Delete → Remove Reference。
2. 右键根上的 `ShoppingGuide` 蓝色工程图标 → **Add Files to "ShoppingGuide"…**
3. 进入 `client/ShoppingGuide/`，多选 6 个子目录：`App` `Features`
   `Networking` `Models` `Components` `Resources`。
4. 对话框底部：
   - Destination：**取消勾选** "Copy items if needed"
   - Added folders：选 **"Create groups"**
   - Add to targets：勾上 `ShoppingGuide`
5. Add。

### 步骤 5 · 工程设置 & 第一次运行

1. 选中根蓝色图标 → **TARGETS → ShoppingGuide → General → Minimum
   Deployments → iOS 16.0**。
2. 顶部 scheme 选 `ShoppingGuide` + 任意 iOS 16+ 模拟器（如 iPhone 15）。
3. Cmd+R 编译运行。预期模拟器显示：
   - 一个购物袋 SF Symbol 图标
   - 文字 **"Shopping Guide · Phase 0 就绪"**
   - 一行小字 `Backend: http://127.0.0.1:8000`

看到这个界面即视作 **Phase 0 客户端验收通过**。

### 步骤 6 · 提交工程文件

```bash
cd <repo>
git add client/ShoppingGuide.xcodeproj client/ShoppingGuide
git status   # 确认没有 client/ShoppingGuide/.git/ 嵌套子仓库
git commit -m "client: bootstrap Xcode project for Phase 0"
```

`.xcodeproj` 内的 `xcuserdata/` 是个人偏好（编辑器折叠、scheme 选择等），仓库
顶层 `.gitignore` 已统一忽略，无需在 client/ 下额外配置。

> Phase 3 最小闭环未引入 SPM 依赖，文字气泡用 SwiftUI 自带 `Text`，远程图片走 `AsyncImage`。`MarkdownUI` / `Kingfisher` 留到 Phase 5 富文本 / 性能打磨阶段再加。

## Phase 3 验收：流式聊天 + 商品卡片

Phase 3 已在 `client/ShoppingGuide/` 下落地：

```
ShoppingGuide/
├── App/
│   ├── ShoppingGuideApp.swift     # @main → NavigationStack { ChatView(env:) }
│   └── AppEnvironment.swift
├── Models/                         # ChatMessage / ProductCard / SSEEvent / ClarifyPayload
├── Networking/                     # APIClient / StreamingClient / SSEParser / UploadService / AudioService
└── Features/
    ├── Chat/                       # ChatView / ChatViewModel / ChatTransport / MessageBubble / ImagePicker / Speech*
    └── Product/                    # ProductCardView / ProductDetailView
```

平级新增了 `client/Package.swift`，是独立 SwiftPM 包，**共用** `ShoppingGuide/` 下源文件（不复制不分叉），用来在 macOS 上跑客户端逻辑层单测；测试代码在 `client/Tests/ShoppingGuideKitTests/`。Xcode 工程通过 `PBXFileSystemSynchronizedRootGroup` 自动同步整个 `ShoppingGuide/` 目录，不需要手动 Add Files。

### 模拟器跑通

```bash
# 1. 后端起来（另一个窗口）
cd ../server && source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2. Xcode 打开，选 iPhone 17 模拟器，Cmd+R
open ShoppingGuide.xcodeproj
```

输入"推荐一款适合油皮的洗面奶" → 看到逐字流式回复 + 商品卡片（带主图 / 价格 / 规格）→ 点击卡片 push 详情页。

### 命令行端到端 smoke

`ChatView` 在 DEBUG 编译时检测 launch arg `-autoSendDemo "<query>"`，可一键复现验收路径：

```bash
DEV=/Applications/Xcode.app/Contents/Developer
UDID=$($DEV/usr/bin/xcrun simctl list devices available \
       | grep "iPhone 17 " | head -1 | grep -oE "[0-9A-F-]{36}")
DEVELOPER_DIR=$DEV xcrun simctl boot "$UDID" 2>/dev/null
DEVELOPER_DIR=$DEV xcodebuild -project ShoppingGuide.xcodeproj -scheme ShoppingGuide \
  -destination "platform=iOS Simulator,name=iPhone 17" -configuration Debug build
APP=$(find ~/Library/Developer/Xcode/DerivedData -name ShoppingGuide.app -path "*Debug-iphonesimulator*" | head -1)
DEVELOPER_DIR=$DEV xcrun simctl install "$UDID" "$APP"
DEVELOPER_DIR=$DEV xcrun simctl launch "$UDID" com.yute.ShoppingGuide \
  -autoSendDemo "推荐一款适合油皮的洗面奶"
sleep 15
DEVELOPER_DIR=$DEV xcrun simctl io "$UDID" screenshot /tmp/phase3.png && open /tmp/phase3.png
```

### 单测（不用 Xcode UI 也能跑）

```bash
cd "$(git rev-parse --show-toplevel)/client"
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
# 期望：49 用例全过（SSEParser 12 + ChatViewModel 20 + MarkdownParser 15 + ProductNavigation 2）
```

## Phase 5C 验收：语音输入 + TTS 播报

Phase 5C 已在客户端接入服务端音频网关：

| 文件 | 作用 |
| --- | --- |
| `Networking/AudioService.swift` | `transcribe(pcm:)` 上传 `/api/v1/audio/asr`，`synthesize(text:voice:)` 调 `/api/v1/audio/tts` 并返回 WAV |
| `Features/Chat/SpeechRecognitionService.swift` | `AVAudioEngine` 录音并转 16k / 16-bit / mono PCM；系统 `SFSpeechRecognizer` 仅作为录音链路不可用时的降级 |
| `Features/Chat/SpeechSynthesisService.swift` | 定义 10 个服务端音色，拉取 WAV 后用 `AVAudioPlayer` 播放，并缓存最近 8 条音频 |
| `Features/Chat/ChatView.swift` | Header 提供音色菜单和自动播报开关；输入栏提供麦克风按钮；assistant 气泡提供单条播报按钮 |

手动验收路径：

1. 先启动后端，并确认 `/api/v1/audio/voices` 可访问。
2. Xcode 启动 App，首次点麦克风时允许麦克风权限。
3. 点麦克风开始录音，再点一次停止；预期 ASR 文本回填输入框。
4. 发送问题后，点 assistant 气泡的 speaker 按钮；预期听到服务端 TTS 播报。
5. Header 切换音色并打开自动播报；下一条 assistant 回复结束后应自动播放。

### Phase 3 踩到的坑

1. **`\r\n` 被 Swift 当成单个 grapheme cluster**：sse-starlette 用 `\r\n` 作行尾，`String.split(separator: "\n")` 直接拆不开（grapheme cluster 不会被中间断开）。修复：parser 先 `replacingOccurrences("\r\n" → "\n")` 归一化，再 split。
2. **`StreamingClient` 立即被释放**：`LiveChatTransport.stream` 里 `let client = StreamingClient(); return client.stream(...)`，client 是局部变量，return 后没人持有，URLSession 跟着失效，delegate 永远不会被回调。修复：`AsyncStream` 的 `onTermination` 闭包**强引用** self（不用 `[weak self]`），让生命周期延到流结束。
3. **SSE 帧分隔可能是 `\r\n\r\n` 或 `\n\n`**：StreamingClient 缓冲区扫描时两种都试，谁先出现先切谁。
4. **`JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase` 对 `[String: String]` 不生效**：只在 Codable 结构体解码时把 `session_id` 转成 `sessionId`；用 dict 接收时仍是原 `session_id` 键。修复：解事件层面用字典时直接读 snake_case。

## 目录索引（Phase 0 已就位）

```
client/
├── ShoppingGuide.xcodeproj             # Xcode 工程文件（本地生成，需提交）
└── ShoppingGuide/                      # 源码包，Xcode 16 自动同步整目录
    ├── App/
    │   ├── ShoppingGuideApp.swift      # @main + Scene + Phase 0 占位首页
    │   └── AppEnvironment.swift        # 全局环境（BaseURL / sessionID）
    ├── Features/                       # Chat/Product/ImagePicker/Speech*
    ├── Networking/                     # Chat stream / upload / audio
    ├── Models/
    ├── Components/
    └── Resources/
        └── Assets.xcassets/
            ├── Contents.json                       # catalog meta
            ├── AppIcon.appiconset/Contents.json    # 空占位，Phase 3 替换为真图标
            └── AccentColor.colorset/Contents.json  # 空占位，Phase 3 设主题色
```

## 已知坑位 & 排错

- **`directory already exists`**：步骤 2 把存储位置选到了 `client/`。改成临时
  目录，按步骤 3 单独挪 `.xcodeproj` 过来。
- **`client/ShoppingGuide/.git/` 出现了**：步骤 2 没取消勾选 "Create Git
  repository on my Mac"。`rm -rf client/ShoppingGuide/.git` 清掉即可，源文件
  不动。
- **`Type 'AppEnvironment' does not conform to protocol 'ObservableObject'` /
  `missing import of defining module 'Combine'`**：Swift 5.9+ 对 SwiftUI 隐式
  重导出 Combine 不再宽容。仓库源码已显式 `import Combine`，如果你看到这个
  报错说明拉到了旧版，确认 `App/AppEnvironment.swift` 顶部有 `import Combine`
  即可。
- **`None of the input catalogs contained a matching ... icon set ... named
  "AppIcon"`**：Assets 目录缺 `AppIcon.appiconset/`。仓库已经补了空占位，但
  Xcode 增量构建对 `.xcassets` 内新增的子目录不会自动重算 actool 依赖。**必须
  做一次 `Product → Clean Build Folder`（Cmd+Shift+K，不是 Cmd+K）**，然后
  Cmd+R 重跑。
- **想推倒重来**：删掉 `client/ShoppingGuide.xcodeproj` 和（如果有）
  `client/ShoppingGuide/.git`，从步骤 2 重新开始；源码骨架本身不会动到。
