import SwiftUI

/// 单条消息气泡。
/// - 用户气泡：右对齐 / 蓝底白字
/// - Assistant 气泡：左对齐 / 灰底，末尾按 isStreaming 接闪烁光标
/// - 卡片：Assistant 气泡下方独立堆叠
/// - clarify：渲染为下方的 chip 按钮组（点击会通过 onSelectClarify 回传）
struct MessageBubble: View {
    let message: ChatMessage
    var onSelectClarify: ((String) -> Void)? = nil

    var body: some View {
        VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 8) {
            HStack {
                if message.role == .user { Spacer(minLength: 40) }
                bubbleText
                if message.role != .user { Spacer(minLength: 40) }
            }
            if !message.productCards.isEmpty {
                VStack(spacing: 8) {
                    ForEach(message.productCards) { card in
                        ProductCardView(card: card)
                    }
                }
                .padding(.horizontal, 4)
            }
            if let payload = message.clarify {
                clarifyChips(payload)
            }
            if let notice = message.errorNotice {
                Text(notice)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(.horizontal, 12)
            }
        }
    }

    private var bubbleText: some View {
        let isUser = message.role == .user
        var displayText = message.text
        // 流式时给文本末尾接一个光标占位，停止后移除
        if message.isStreaming && message.text.isEmpty == false {
            displayText += " ▍"
        } else if message.isStreaming {
            displayText = "▍"
        }
        return Text(displayText)
            .font(.body)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .foregroundStyle(isUser ? .white : .primary)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(isUser ? Color.accentColor : Color(.secondarySystemBackground))
            )
            .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)
            .padding(.horizontal, 4)
    }

    @ViewBuilder
    private func clarifyChips(_ payload: ClarifyPayload) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(payload.question)
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack(spacing: 8) {
                ForEach(payload.options, id: \.self) { opt in
                    Button(opt) {
                        onSelectClarify?(opt)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
            }
        }
        .padding(.horizontal, 12)
    }
}
