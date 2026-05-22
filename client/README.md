# iOS 客户端 · 启动说明

## 环境要求

- Xcode **15+**（实测 Xcode 16 / objectVersion 77 也 OK）
- macOS 14+
- iOS 部署目标 **16.0+**
- Swift **5.9**

## Phase 0 验收：跑通空白页

由于 `.xcodeproj` 是二进制 plist，无法在仓库里手写生成，本仓库只提交了
`ShoppingGuide/` 下的 Swift 源码骨架（详见文末"目录索引"）。首次本地准备
需要在 Xcode 里生成一份 `.xcodeproj`，再让它指向仓库里的源码目录。

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

> 待 Phase 3 开始时再补 SPM 依赖：`MarkdownUI`、`Kingfisher`。

## 目录索引（Phase 0 已就位）

```
client/
├── ShoppingGuide.xcodeproj             # Xcode 工程文件（本地生成，需提交）
└── ShoppingGuide/                      # 源码包，Xcode 16 自动同步整目录
    ├── App/
    │   ├── ShoppingGuideApp.swift      # @main + Scene + Phase 0 占位首页
    │   └── AppEnvironment.swift        # 全局环境（BaseURL / sessionID）
    ├── Features/                       # 后续阶段填充（Chat/Product/Multimodal/Cart）
    ├── Networking/
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
