import SwiftUI

/// PriceCat 视觉系统：色板、字体、圆角、阴影 token 集中管理。
///
/// 命名与 Figma 等设计稿同步：所有视图引用 Theme.color.xxx / Theme.radius.xxx，
/// 不允许在视图里硬编码十六进制或具体字号；统一改主题色只动这一个文件。
///
/// 设计风格：致敬 Coupert，暖橙主色 + 浅橙白底 + 价格高对比红 + 省钱绿。
enum Theme {

    // MARK: - Colors

    enum Palette {
        /// 品牌橙 #FF6B00：CTA 按钮 / brand icon / 重要 chip
        static let brand = Color(hex: 0xFF6B00)
        /// 浅橙 #FF8533：hover、被按下、第二层强调
        static let brandSoft = Color(hex: 0xFF8533)
        /// 价格红 #FF3D00：商品现价、限时角标
        static let priceHot = Color(hex: 0xFF3D00)
        /// 省钱绿 #00B86B：saved badge、价格下降提示
        static let savingsGreen = Color(hex: 0x00B86B)

        /// 整页背景 #FFF8F2 浅橙白
        static let canvas = Color(hex: 0xFFF8F2)
        /// 卡片底白
        static let surface = Color.white
        /// 浅边框 / divider
        static let border = Color(hex: 0xF0E6DC)
        /// chip 浅橙底 #FFF1E5
        static let chipSoft = Color(hex: 0xFFF1E5)

        /// 主文字
        static let textPrimary = Color(hex: 0x1F1A17)
        /// 次文字
        static let textSecondary = Color(hex: 0x8A7E73)
        /// 占位文字
        static let textPlaceholder = Color(hex: 0xC7BBB0)
        /// 用户气泡上的白文字
        static let onBrand = Color.white
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
        /// 轻量卡片阴影：底 / 上浮 1pt
        static let card = ShadowStyle(color: .black.opacity(0.05), radius: 8, x: 0, y: 2)
        /// 浮起按钮
        static let lifted = ShadowStyle(color: Palette.brand.opacity(0.25), radius: 12, x: 0, y: 6)
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
