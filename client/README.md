# iOS 客户端 · 启动说明

## 环境要求

- Xcode **15+**（macOS 14+）
- iOS 部署目标 **16.0+**
- Swift **5.9**

## Phase 0 验收：跑通空白页

由于 `.xcodeproj` 是二进制 plist，无法在仓库里手写生成，本仓库只提交了
`ShoppingGuide/` 下的 Swift 源码骨架。**首次本地准备步骤**：

1. 打开 Xcode → `File → New → Project…`
2. 选择 **App** 模板，下一步填：
   - Product Name：`ShoppingGuide`
   - Interface：`SwiftUI`
   - Language：`Swift`
   - Storage：`None`
3. **存储位置选当前 `client/` 目录**，Xcode 会在 `client/ShoppingGuide.xcodeproj`
   创建工程文件，且自动生成与本仓库同名的 `ShoppingGuide/` 文件夹。
4. 把 Xcode 自动生成的默认文件删除，然后把仓库 `ShoppingGuide/` 下已有的源码
   拖入工程（Add files to "ShoppingGuide"，**勾选** Copy items if needed 的反向：
   选择 “Create groups”，不重复 Copy，保持源码在 git 仓库内）。
5. 在 Project Settings → General → Deployment Info 设为 `iOS 16.0`。
6. 编译运行：Cmd+R，预期模拟器显示 **“Shopping Guide · Phase 0 就绪”** 的占位
   界面，即视作 Phase 0 客户端验收通过。

> 待 Phase 3 开始时再补 SPM 依赖：`MarkdownUI`、`Kingfisher`。

## 目录索引（Phase 0 已就位）

```
client/ShoppingGuide/
├── App/
│   ├── ShoppingGuideApp.swift     # @main + Scene
│   └── AppEnvironment.swift       # 全局环境（BaseURL / sessionId）
├── Features/                      # 后续阶段填充
├── Networking/
├── Models/
├── Components/
└── Resources/
    └── Assets.xcassets/
```
