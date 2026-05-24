import SwiftUI

/// 单条消息气泡（Coupert 风格）。
///
/// 关键修复（旧版 bug）：旧实现给 `Text` 加了 `.frame(maxWidth: .infinity, alignment: ...)`，
/// 这会把气泡背景拉成整行宽度，导致"一句话气泡也撑满屏幕"。
/// 现在改用 `HStack { Spacer/Bubble }` 控对齐，让气泡的 `RoundedRectangle.fill`
/// 只 hug `Text` 自身的尺寸。
///
/// 视觉差异：
/// - 用户气泡：品牌橙底 (#FF6B00) 白字，右对齐，最大 78% 屏宽。
/// - Assistant 气泡：白底 + 浅边框，左对齐。
/// - 流式时末尾接一个微动的圆点光标（不是字符 `▍`，避免视觉抖动）。
/// - 商品卡片与 clarify chips 不嵌进气泡，独立排列在气泡下方。
struct MessageBubble: View {
    let message: ChatMessage
    var onSelectClarify: ((String) -> Void)? = nil

    var body: some View {
        VStack(alignment: message.role == .user ? .trailing : .leading,
               spacing: Theme.Spacing.s) {
            bubbleRow
            if !message.productCards.isEmpty {
                cardsStack
            }
            if let payload = message.clarify {
                clarifyChips(payload)
            }
            if let notice = message.errorNotice {
                errorRow(notice)
            }
        }
    }

    // MARK: - Bubble

    private var bubbleRow: some View {
        HStack(alignment: .top, spacing: 0) {
            if message.role == .user { Spacer(minLength: Theme.Spacing.xl) }
            bubbleContent
            if message.role != .user { Spacer(minLength: Theme.Spacing.xl) }
        }
    }

    @ViewBuilder
    private var bubbleContent: some View {
        let isUser = message.role == .user
        // Trim 末尾空白：后端虽已 rstrip，留这里做展示层 fallback。
        let displayText = message.text.trimmingCharacters(in: .whitespacesAndNewlines)

        // 用户气泡用纯 Text（用户输入不需 markdown 渲染）；assistant 走 MarkdownView 支持表格。
        VStack(alignment: .leading, spacing: 6) {
            if !displayText.isEmpty {
                if isUser {
                    Text(displayText)
                        .font(Theme.Typo.body())
                        .foregroundStyle(Theme.Palette.onBrand)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    MarkdownView(text: displayText, textColor: Theme.Palette.textPrimary)
                }
            }
            if message.isStreaming {
                StreamingDot(color: isUser ? Theme.Palette.onBrand : Theme.Palette.brand)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.bubble, style: .continuous)
                .fill(isUser ? AnyShapeStyle(Theme.Palette.brand) : AnyShapeStyle(Theme.Palette.surface))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.bubble, style: .continuous)
                .stroke(isUser ? Color.clear : Theme.Palette.border, lineWidth: 1)
        )
        .themeShadow(isUser ? .init(color: .clear, radius: 0, x: 0, y: 0) : Theme.Shadow.card)
    }

    // MARK: - Cards / Clarify / Error

    private var cardsStack: some View {
        VStack(spacing: Theme.Spacing.m) {
            ForEach(message.productCards) { card in
                ProductCardView(card: card)
            }
        }
    }

    @ViewBuilder
    private func clarifyChips(_ payload: ClarifyPayload) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.s) {
            Text(payload.question)
                .font(Theme.Typo.caption())
                .foregroundStyle(Theme.Palette.textSecondary)
            FlowLayout(spacing: Theme.Spacing.s) {
                ForEach(payload.options, id: \.self) { opt in
                    Button {
                        onSelectClarify?(opt)
                    } label: {
                        Text(opt)
                            .font(Theme.Typo.caption(.semibold))
                            .foregroundStyle(Theme.Palette.brand)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 6)
                            .background(
                                Capsule().fill(Theme.Palette.chipSoft)
                            )
                            .overlay(
                                Capsule().stroke(Theme.Palette.brand.opacity(0.3), lineWidth: 1)
                            )
                    }
                }
            }
        }
        .padding(.horizontal, 4)
    }

    private func errorRow(_ notice: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "exclamationmark.circle.fill")
                .font(.system(size: 12))
            Text(notice).font(Theme.Typo.caption())
        }
        .foregroundStyle(Theme.Palette.priceHot)
        .padding(.horizontal, 12)
    }
}

// MARK: - Streaming dot animation

/// 三段呼吸圆点：替代旧的字符 `▍`，避免气泡尺寸随光标抖动。
private struct StreamingDot: View {
    let color: Color
    @State private var phase: CGFloat = 0

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 6, height: 6)
            .opacity(0.3 + 0.7 * phase)
            .onAppear {
                withAnimation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true)) {
                    phase = 1
                }
            }
    }
}

// MARK: - FlowLayout (chips wrap)

/// 极简 wrap 布局：chip 超宽自动换行。iOS 16+ 用 SwiftUI Layout 协议。
struct FlowLayout: Layout {
    let spacing: CGFloat
    init(spacing: CGFloat = 8) { self.spacing = spacing }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, lineHeight: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if x + s.width > maxWidth, x > 0 {
                x = 0; y += lineHeight + spacing; lineHeight = 0
            }
            x += s.width + spacing
            lineHeight = max(lineHeight, s.height)
        }
        return CGSize(width: maxWidth.isFinite ? maxWidth : x, height: y + lineHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, lineHeight: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if x + s.width > bounds.maxX, x > bounds.minX {
                x = bounds.minX; y += lineHeight + spacing; lineHeight = 0
            }
            sub.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing
            lineHeight = max(lineHeight, s.height)
        }
    }
}
