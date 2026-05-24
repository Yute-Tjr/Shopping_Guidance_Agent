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
            // Assistant 消息按 markdown 块拆开："段落组进气泡 / 表格独立全宽"，
            // 避免长表格被气泡 minLength 限宽挤成竖排的"每字一行"。
            if message.role == .assistant {
                assistantContent
            } else {
                bubbleRow
            }
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

    // MARK: - Assistant content (split by markdown blocks)

    @ViewBuilder
    private var assistantContent: some View {
        let trimmed = message.text.trimmingCharacters(in: .whitespacesAndNewlines)
        let blocks = MarkdownParser.parse(trimmed)
        let segments = Self.segregate(blocks: blocks)

        VStack(alignment: .leading, spacing: Theme.Spacing.s) {
            ForEach(Array(segments.enumerated()), id: \.offset) { _, seg in
                switch seg {
                case .paragraphs(let attrs):
                    // 同一组连续段落塞进同一个气泡，气泡 hug 内容，min 宽=不限
                    assistantBubble(paragraphs: attrs)
                case .table(let headers, let rows):
                    // 表格占消息列全宽 —— 突破气泡的右侧 spacer，让内容能展开
                    TableBlockView(headers: headers, rows: rows)
                }
            }
            // 流式光标 = 没有段落或最后一块不是段落时，独立放一个
            if message.isStreaming && !endsWithBubble(segments) {
                StreamingDot(color: Theme.Palette.brand)
            }
        }
    }

    private func endsWithBubble(_ segments: [MarkdownSegment]) -> Bool {
        if case .paragraphs = segments.last { return true }
        return false
    }

    private func assistantBubble(paragraphs: [AttributedString]) -> some View {
        HStack(alignment: .top, spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(Array(paragraphs.enumerated()), id: \.offset) { _, p in
                    Text(p)
                        .font(Theme.Typo.body())
                        .foregroundStyle(Theme.Palette.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if message.isStreaming {
                    StreamingDot(color: Theme.Palette.brand)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: Theme.Radius.bubble, style: .continuous)
                    .fill(Theme.Palette.surface)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Radius.bubble, style: .continuous)
                    .stroke(Theme.Palette.border, lineWidth: 1)
            )
            .themeShadow(Theme.Shadow.card)
            Spacer(minLength: Theme.Spacing.xl)
        }
    }

    // MARK: - segregate

    enum MarkdownSegment {
        case paragraphs([AttributedString])
        case table(headers: [String], rows: [[String]])
    }

    /// 把 MarkdownBlock 列表压缩为「连续段落组 + 表格」两类 segment，
    /// 便于按视觉层级渲染：段落进同一气泡、表格独占全宽。
    private static func segregate(blocks: [MarkdownBlock]) -> [MarkdownSegment] {
        var out: [MarkdownSegment] = []
        var buffer: [AttributedString] = []
        for b in blocks {
            switch b {
            case .paragraph(let attr):
                buffer.append(attr)
            case .table(let h, let r):
                if !buffer.isEmpty {
                    out.append(.paragraphs(buffer))
                    buffer.removeAll()
                }
                out.append(.table(headers: h, rows: r))
            }
        }
        if !buffer.isEmpty {
            out.append(.paragraphs(buffer))
        }
        return out
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
