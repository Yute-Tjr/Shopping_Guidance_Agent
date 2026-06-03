import SwiftUI

/// PriceCat 视觉系统：色板、字体、圆角、阴影 token 集中管理。
///
/// 命名与 Figma 等设计稿同步：所有视图引用 Theme.color.xxx / Theme.radius.xxx，
/// 不允许在视图里硬编码十六进制或具体字号；统一改主题色只动这一个文件。
///
/// 设计风格：贴合 PriceCat 图标，鼠尾草绿底 + 黑猫墨色 + 眼睛黄绿 + 麦克风信号绿。
enum Theme {

    // MARK: - Colors

    enum Palette {
        /// 黑猫墨色：CTA / 用户气泡 / 高优先级图标
        static let brand = Color(hex: 0x171A19)
        /// 耳机炭灰：二级强调与渐变过渡
        static let brandSoft = Color(hex: 0x3F4346)
        /// 猫眼黄绿：选中态、音色入口、轻量强调
        static let highlight = Color(hex: 0xC9CC68)
        /// 暖铜红：商品现价、错误提示
        static let priceHot = Color(hex: 0xB85B42)
        /// 麦克风信号绿：saved badge、录音/可用状态
        static let savingsGreen = Color(hex: 0x1FD86A)

        /// 整页背景：从图标墙面抽出的浅鼠尾草绿
        static let canvas = Color(hex: 0xE7EFE2)
        /// 卡片底：略带暖度的白
        static let surface = Color(hex: 0xFBFCF8)
        /// 浅边框 / divider
        static let border = Color(hex: 0xC9D4C4)
        /// chip 浅绿底
        static let chipSoft = Color(hex: 0xF0F6EA)
        /// 输入栏 / 表格浅底
        static let surfaceTint = Color(hex: 0xDCE8D5)

        /// 主文字
        static let textPrimary = Color(hex: 0x151816)
        /// 次文字
        static let textSecondary = Color(hex: 0x657165)
        /// 占位文字
        static let textPlaceholder = Color(hex: 0x9CAB98)
        /// 用户气泡上的浅色文字
        static let onBrand = Color(hex: 0xF8FAF2)
    }

    // MARK: - Typography

    /// 全局走 .serif → iOS 系统会映射到 **New York**（Apple 自家衬线，质感对标
    /// Claude.ai 用的 Tiempos Text）。配合品牌橙与浅橙底，整页 editorial / 杂志感，
    /// 主动远离常见 AI 应用的 sans-serif "AI slop"。
    enum Typo {
        static func display(_ weight: Font.Weight = .semibold) -> Font {
            .system(size: 24, weight: weight, design: .serif)
        }
        static func title(_ weight: Font.Weight = .semibold) -> Font {
            .system(size: 18, weight: weight, design: .serif)
        }
        static func body(_ weight: Font.Weight = .regular) -> Font {
            .system(size: 16, weight: weight, design: .serif)
        }
        static func caption(_ weight: Font.Weight = .regular) -> Font {
            .system(size: 12, weight: weight, design: .serif)
        }
        /// 商品价格强调 —— 衬线 bold 显得克制有教养
        static let priceLg = Font.system(size: 22, weight: .bold, design: .serif)
        static let priceMd = Font.system(size: 16, weight: .semibold, design: .serif)
        /// 品牌 wordmark —— italic semibold，杂志刊头风
        static let brandWordmark = Font.system(size: 22, weight: .semibold, design: .serif).italic()
        /// 表头 —— 表格用，比 body 略小但加粗
        static let tableHeader = Font.system(size: 14, weight: .semibold, design: .serif)
        /// 表格单元格
        static let tableCell = Font.system(size: 14, weight: .regular, design: .serif)
    }

    // MARK: - Radii / Spacing / Shadows

    enum Radius {
        static let chip: CGFloat = 8
        static let card: CGFloat = 16
        static let hero: CGFloat = 20
        static let bubble: CGFloat = 18
        static let pill: CGFloat = 999
    }

    enum Spacing {
        static let xs: CGFloat = 4
        static let s: CGFloat = 8
        static let m: CGFloat = 12
        static let l: CGFloat = 16
        static let xl: CGFloat = 24
        static let xxl: CGFloat = 32
    }

    enum Shadow {
        /// 轻量卡片阴影：低对比、贴近油画图标的柔和质感
        static let card = ShadowStyle(color: Palette.brand.opacity(0.08), radius: 10, x: 0, y: 3)
        /// 浮起按钮
        static let lifted = ShadowStyle(color: Palette.brand.opacity(0.22), radius: 14, x: 0, y: 7)
    }

    struct ShadowStyle {
        let color: Color
        let radius: CGFloat
        let x: CGFloat
        let y: CGFloat
    }
}

// MARK: - View helpers

extension View {
    /// 给视图加 Theme.Shadow 定义的阴影。
    func themeShadow(_ style: Theme.ShadowStyle) -> some View {
        self.shadow(color: style.color, radius: style.radius, x: style.x, y: style.y)
    }
}

extension Color {
    /// Hex 字面量构造：Color(hex: 0xFF6B00)。
    init(hex: UInt32, alpha: Double = 1.0) {
        let r = Double((hex >> 16) & 0xFF) / 255.0
        let g = Double((hex >> 8) & 0xFF) / 255.0
        let b = Double(hex & 0xFF) / 255.0
        self.init(.sRGB, red: r, green: g, blue: b, opacity: alpha)
    }
}
