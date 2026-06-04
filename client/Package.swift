// swift-tools-version:5.9
//
// SwiftPM 包定义，仅用于在 macOS 上跑客户端逻辑层单测。
// 注意：iOS App 仍由同级的 ShoppingGuide.xcodeproj 编译运行，
// 本 Package 与 Xcode 工程**共用** ShoppingGuide/ 目录下的源文件（不复制不分叉）。
//
// 使用：
//   cd client
//   swift test
//
// 设计要点：
// - 平台仅 macOS：被测代码必须避免 UIKit 直接引用（用 Foundation/Combine/SwiftUI 跨平台 API）。
//   AppEnvironment.swift 是唯一引用 UIDevice 的文件，在 exclude 里剔除。
// - 视图层（ChatView / MessageBubble / ProductCard*View）在 Xcode 里写 UI 预览即可，
//   逻辑测试只覆盖 Models / Networking / ChatViewModel。
import PackageDescription

let package = Package(
    name: "ShoppingGuideKit",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "ShoppingGuideKit", targets: ["ShoppingGuideKit"])
    ],
    targets: [
        .target(
            name: "ShoppingGuideKit",
            path: "ShoppingGuide",
            exclude: [
                "App",
                "Features/Chat/ChatView.swift",
                "Features/Chat/MessageBubble.swift",
                "Features/Chat/ClarifyChipsView.swift",
                "Features/Chat/ImagePicker.swift",   // 引 UIKit/PhotosUI，macOS 测试不编译
                "Features/Product/ProductCardView.swift",
                "Features/Product/ProductDetailView.swift",
                "Components/MarkdownView.swift",
                "Resources/Assets.xcassets",
                "Resources/app_begin.mp4",
            ],
            sources: [
                "Models",
                "Networking",
                "Features/Chat",
                "Components",       // MarkdownParser 纯逻辑可测
                "Resources",         // Theme.swift
            ]
        ),
        .testTarget(
            name: "ShoppingGuideKitTests",
            dependencies: ["ShoppingGuideKit"],
            path: "Tests/ShoppingGuideKitTests"
        ),
    ]
)
