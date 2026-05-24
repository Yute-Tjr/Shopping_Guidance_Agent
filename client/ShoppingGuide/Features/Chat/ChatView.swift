import SwiftUI

/// PriceCat 聊天主页：自绘品牌头 + 消息流 + 暖色输入栏。
///
/// 不用 NavigationBar 自带 title（视觉太单薄）。顶部一行品牌头 = 渐变橙猫脸图标
/// + "PriceCat" 重字 wordmark + 副标"AI 比价导购"，右侧"新建会话"按钮，整行下方
/// 用浅边线收尾。空状态居中放一组可点击的 starter chips，给用户具体可问的范例。
struct ChatView: View {
    @EnvironmentObject private var env: AppEnvironment
    @StateObject private var viewModel: ChatViewModel
    @FocusState private var inputFocused: Bool

    /// 4 条预置 query。后续 Phase 4 可换成"近期热搜"或个性化推荐。
    private let starterPrompts: [String] = [
        "推荐一款适合油皮的洗面奶",
        "200 元以下的蓝牙耳机",
        "对比一下兰蔻和雅诗兰黛的精华",
        "送女朋友的口红选什么色号",
    ]

    init(env: AppEnvironment) {
        let api = APIClient(baseURL: env.baseURL)
        let transport = LiveChatTransport(api: api)
        _viewModel = StateObject(wrappedValue: ChatViewModel(
            transport: transport,
            initialSessionID: env.sessionID
        ))
    }

    var body: some View {
        ZStack(alignment: .top) {
            Theme.Palette.canvas.ignoresSafeArea()

            VStack(spacing: 0) {
                brandHeader
                Divider().background(Theme.Palette.border)
                messageList
                inputBar
            }
        }
        .navigationBarHidden(true)
        .task {
            #if DEBUG
            // E2E smoke 钩子：simctl 启动时若传 -autoSendDemo "<query>"，自动发一次。
            if let idx = CommandLine.arguments.firstIndex(of: "-autoSendDemo"),
               CommandLine.arguments.indices.contains(idx + 1) {
                let demo = CommandLine.arguments[idx + 1]
                try? await Task.sleep(nanoseconds: 400_000_000)
                viewModel.inputText = demo
                await viewModel.send()
            }
            #endif
        }
    }

    // MARK: - Brand header

    private var brandHeader: some View {
        HStack(spacing: Theme.Spacing.m) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [Theme.Palette.brand, Theme.Palette.brandSoft],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: 38, height: 38)
                Image(systemName: "cat.fill")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(.white)
            }
            .themeShadow(Theme.Shadow.lifted)

            VStack(alignment: .leading, spacing: 0) {
                Text("PriceCat")
                    .font(Theme.Typo.brandWordmark)
                    .foregroundStyle(Theme.Palette.textPrimary)
                Text("AI 比价导购")
                    .font(Theme.Typo.caption())
                    .foregroundStyle(Theme.Palette.textSecondary)
            }

            Spacer()

            Button {
                viewModel.resetSession()
            } label: {
                Image(systemName: "plus.bubble.fill")
                    .font(.system(size: 18))
                    .foregroundStyle(Theme.Palette.brand)
                    .padding(8)
                    .background(
                        Circle().fill(Theme.Palette.chipSoft)
                    )
            }
            .accessibilityLabel("新建会话")
        }
        .padding(.horizontal, Theme.Spacing.l)
        .padding(.vertical, Theme.Spacing.m)
        .background(Theme.Palette.canvas)
    }

    // MARK: - Message list

    private var messageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Theme.Spacing.l) {
                    if viewModel.messages.isEmpty {
                        emptyState
                            .padding(.top, 40)
                    }
                    ForEach(viewModel.messages) { msg in
                        MessageBubble(message: msg) { option in
                            viewModel.inputText = option
                            Task { await viewModel.send() }
                        }
                        .id(msg.id)
                    }
                }
                .padding(.horizontal, Theme.Spacing.l)
                .padding(.vertical, Theme.Spacing.m)
            }
            .onChange(of: viewModel.messages.last?.text) { _, _ in
                if let last = viewModel.messages.last {
                    withAnimation(.easeOut(duration: 0.18)) {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
            .onChange(of: viewModel.messages.count) { _, _ in
                if let last = viewModel.messages.last {
                    proxy.scrollTo(last.id, anchor: .bottom)
                }
            }
        }
    }

    // MARK: - Empty state

    private var emptyState: some View {
        VStack(spacing: Theme.Spacing.l) {
            ZStack {
                Circle()
                    .fill(Theme.Palette.chipSoft)
                    .frame(width: 96, height: 96)
                Image(systemName: "cat.fill")
                    .font(.system(size: 44, weight: .bold))
                    .foregroundStyle(
                        LinearGradient(
                            colors: [Theme.Palette.brand, Theme.Palette.brandSoft],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
            }

            VStack(spacing: 6) {
                Text("喵～想买点什么？")
                    .font(Theme.Typo.display())
                    .foregroundStyle(Theme.Palette.textPrimary)
                Text("PriceCat 帮你从 100 款真实商品里挑")
                    .font(Theme.Typo.caption())
                    .foregroundStyle(Theme.Palette.textSecondary)
            }

            VStack(spacing: Theme.Spacing.s) {
                ForEach(starterPrompts, id: \.self) { prompt in
                    Button {
                        viewModel.inputText = prompt
                        Task { await viewModel.send() }
                    } label: {
                        HStack {
                            Image(systemName: "sparkles")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(Theme.Palette.brand)
                            Text(prompt)
                                .font(Theme.Typo.body())
                                .foregroundStyle(Theme.Palette.textPrimary)
                            Spacer()
                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(Theme.Palette.textPlaceholder)
                        }
                        .padding(.horizontal, Theme.Spacing.m)
                        .padding(.vertical, 12)
                        .background(
                            RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                                .fill(Theme.Palette.surface)
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                                .stroke(Theme.Palette.border, lineWidth: 1)
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.top, Theme.Spacing.s)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Input bar

    private var inputBar: some View {
        let isInputEmpty = viewModel.inputText.trimmingCharacters(in: .whitespaces).isEmpty
        let canSend = !viewModel.isSending && !isInputEmpty

        return HStack(spacing: Theme.Spacing.s) {
            HStack(spacing: Theme.Spacing.s) {
                TextField(
                    "",
                    text: $viewModel.inputText,
                    prompt: Text("说点什么，比如「200 元蓝牙耳机」")
                        .foregroundColor(Theme.Palette.textPlaceholder),
                    axis: .vertical
                )
                .font(Theme.Typo.body())
                .foregroundStyle(Theme.Palette.textPrimary)
                .lineLimit(1...4)
                .focused($inputFocused)
                .submitLabel(.send)
                .onSubmit { Task { await viewModel.send() } }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: Theme.Radius.pill, style: .continuous)
                    .fill(Theme.Palette.surface)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Radius.pill, style: .continuous)
                    .stroke(inputFocused ? Theme.Palette.brand.opacity(0.5) : Theme.Palette.border,
                            lineWidth: 1)
            )

            Button {
                inputFocused = false
                Task { await viewModel.send() }
            } label: {
                Image(systemName: "arrow.up")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 40, height: 40)
                    .background(
                        Circle()
                            .fill(canSend ? Theme.Palette.brand : Theme.Palette.textPlaceholder)
                    )
                    .themeShadow(canSend ? Theme.Shadow.lifted
                                          : .init(color: .clear, radius: 0, x: 0, y: 0))
            }
            .disabled(!canSend)
            .animation(.easeOut(duration: 0.15), value: canSend)
        }
        .padding(.horizontal, Theme.Spacing.l)
        .padding(.top, 10)
        .padding(.bottom, 10)
        .background(Theme.Palette.canvas)
    }
}
