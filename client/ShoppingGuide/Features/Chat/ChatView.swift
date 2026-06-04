import PhotosUI
import SwiftUI

/// PriceCat 聊天主页：图标品牌头 + 消息流 + 鼠尾草绿输入栏。
///
/// 不用 NavigationBar 自带 title（视觉太单薄）。顶部一行品牌头 = PriceCat icon
/// + "PriceCat" 重字 wordmark + 副标，右侧"新建会话"按钮，整行下方
/// 用浅边线收尾。空状态居中放一组可点击的 starter chips，给用户具体可问的范例。
struct ChatView: View {
    @EnvironmentObject private var env: AppEnvironment
    @StateObject private var viewModel: ChatViewModel
    @FocusState private var inputFocused: Bool

    /// 全局只跑一次的 e2e demo flag。SwiftUI `.task` 在 ChatView 每次重新出现时都
    /// 会重跑（如从 ProductDetailView pop 回来），不锁住的话会把验收 query 反复发送。
    /// 用 static 而非 @State：@State 会随 view 重建归零，static 持续整个进程生命周期。
    private static var autoSendDemoFired = false

    /// 4 条预置 query。图标和强调色集中在 StarterPrompt，避免空状态 UI 写死语义。
    private let starterPrompts = StarterPrompt.defaultItems

    @State private var photosPickerItem: PhotosPickerItem? = nil

    /// 流式期间用户是否手动滚动过：拖动一旦发生就翻 true，停止追着 token 把视图拉回底部；
    /// 用户发新消息时（messages.count 增加）重置为 false 并强制粘底。
    @State private var userScrolledAwayDuringStream: Bool = false
    /// 节流 scrollTo：流式 token 一秒可能 30 个，把 80ms 窗口内的多次合并成一次，
    /// 同时去掉 withAnimation —— 0.18s 动画排队叠加用户手势是主线程死锁的主因。
    @State private var scrollKickTask: Task<Void, Never>? = nil
    @State private var productNavigation = ProductNavigationSelection()

    init(env: AppEnvironment) {
        let api = APIClient(baseURL: env.baseURL)
        let transport = LiveChatTransport(api: api)
        let upload = UploadService(baseURL: env.baseURL)
        let audio = AudioService(baseURL: env.baseURL)
        _viewModel = StateObject(wrappedValue: ChatViewModel(
            transport: transport,
            initialSessionID: env.sessionID,
            uploadService: upload,
            speechRecognizer: ServerSpeechRecognitionService(
                api: audio,
                fallback: SpeechRecognitionService()
            ),
            speechSpeaker: ServerSpeechSynthesisService(api: audio)
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
        .navigationDestination(item: $productNavigation.destination) { destination in
            ProductDetailView(productID: destination.productID)
        }
        // pickedImage 清空后，必须同步把 PhotosPicker 的 selection 也清掉。
        // 否则 send()/resetSession() 只清了 pickedImage，photosPickerItem 还残留旧 asset id；
        // 用户再选同一张图时新 PhotosPickerItem == 旧值，.onChange 不触发，图加载不进来。
        .onChange(of: viewModel.pickedImage) { _, newValue in
            if newValue == nil {
                photosPickerItem = nil
            }
        }
        .task {
            #if DEBUG
            // E2E smoke 钩子：simctl 启动时若传 -autoSendDemo "<query>"，自动发一次。
            // 守 autoSendDemoFired：避免从详情页 pop 回来时 .task 重新触发再发送。
            guard !Self.autoSendDemoFired,
                  let idx = CommandLine.arguments.firstIndex(of: "-autoSendDemo"),
                  CommandLine.arguments.indices.contains(idx + 1) else { return }
            Self.autoSendDemoFired = true
            let demo = CommandLine.arguments[idx + 1]
            try? await Task.sleep(nanoseconds: 400_000_000)
            viewModel.inputText = demo
            await viewModel.send()
            #endif
        }
    }

    // MARK: - Brand header

    private var brandHeader: some View {
        HStack(spacing: Theme.Spacing.m) {
            Image("PriceCatIcon")
                .resizable()
                .scaledToFill()
                .frame(width: 42, height: 42)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .stroke(Theme.Palette.surface.opacity(0.9), lineWidth: 1)
                )
            .themeShadow(Theme.Shadow.lifted)

            VStack(alignment: .leading, spacing: 1) {
                Text("PriceCat")
                    .font(Theme.Typo.brandWordmark)
                    .foregroundStyle(Theme.Palette.textPrimary)
                Text("image, voice, and real-listing advice")
                    .font(Theme.Typo.caption().italic())
                    .foregroundStyle(Theme.Palette.textSecondary)
            }

            Spacer()

            Button {
                viewModel.resetSession()
            } label: {
                Image(systemName: "plus.bubble.fill")
                    .font(.system(size: 18))
                    .foregroundStyle(Theme.Palette.onBrand)
                    .frame(width: 36, height: 36)
                    .background(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .fill(Theme.Palette.brand)
                    )
            }
            .accessibilityLabel("新建会话")
        }
        .padding(.horizontal, Theme.Spacing.l)
        .padding(.vertical, Theme.Spacing.m)
        .background(
            LinearGradient(
                colors: [Theme.Palette.canvas, Theme.Palette.chipSoft],
                startPoint: .top,
                endPoint: .bottom
            )
        )
    }

    // MARK: - Message list

    /// 空状态的 scroll anchor id：新建会话后用它把 ScrollView 滚回顶。
    /// 否则上一轮长对话滚到底部的 contentOffset 残留，空状态会被定位到屏幕外。
    private static let emptyStateAnchorID = "PriceCat.emptyState"

    private var messageList: some View {
        ScrollViewReader { proxy in
            // 用 List 而非 ScrollView+LazyVStack：后者在滑动时 SwiftUI 每帧都跑
            // ScrollViewLayoutComputer.sizeThatFits 全量重测 content，trace 测得占主线程 64%
            // → 流式 token 推内容时叠加滑动手势 = 100% CPU 卡死。
            // List 走 UITableView 后端，cell 独立缓存高度，滑动时只测可见 cell。
            List {
                if viewModel.messages.isEmpty {
                    emptyState
                        .padding(.top, 40)
                        .padding(.horizontal, Theme.Spacing.l)
                        .id(Self.emptyStateAnchorID)
                        .listRowSeparator(.hidden)
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets())
                }
                ForEach(viewModel.messages) { msg in
                    MessageBubble(
                        message: msg,
                        onSelectClarify: { option in
                            viewModel.inputText = option
                            Task { await viewModel.send() }
                        },
                        onSelectProduct: { card in
                            productNavigation.select(productID: card.productId)
                        },
                        selectedVoice: viewModel.selectedVoice,
                        onSelectVoice: { voice in
                            viewModel.selectedVoice = voice
                        },
                        onSpeakAssistant: { text in
                            viewModel.speakAssistantText(text)
                        }
                    )
                    .equatable()
                    // 上下各 Spacing.s (8) → 相邻两行合计 16 = 原 LazyVStack(spacing: l) 视觉
                    .padding(.horizontal, Theme.Spacing.l)
                    .padding(.vertical, Theme.Spacing.s)
                    .id(msg.id)
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets())
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)   // 隐藏 List 自带的浅灰背景，让 Theme.canvas 透出
            // 用户在流式期间一旦手动拖动，就停止 auto-scroll，让用户能安心向上看历史。
            // minimumDistance: 10 避免和点击/无关手势冲突；simultaneous 不抢 List 自身滚动。
            .simultaneousGesture(
                DragGesture(minimumDistance: 10).onChanged { _ in
                    if viewModel.isSending {
                        userScrolledAwayDuringStream = true
                    }
                }
            )
            .onChange(of: viewModel.messages.last?.text) { _, _ in
                scheduleScrollToBottom(proxy: proxy)
            }
            .onChange(of: viewModel.messages.count) { oldCount, newCount in
                // 新建会话：count 从 >0 变 0 → 把 ScrollView 滚回 emptyState 顶部
                if newCount == 0 && oldCount > 0 {
                    withAnimation(.easeOut(duration: 0.18)) {
                        proxy.scrollTo(Self.emptyStateAnchorID, anchor: .top)
                    }
                    return
                }
                // 用户发新消息：重置追随状态并立刻粘底（不走节流，让发送瞬间感觉跟手）
                if newCount > oldCount {
                    userScrolledAwayDuringStream = false
                    scrollKickTask?.cancel()
                    if let last = viewModel.messages.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
        }
    }

    /// 把同一帧内多个 token 的 scrollTo 合并成一次（80ms 节流），
    /// 并去掉 withAnimation 避免动画堆栈。用户主动滑离时直接放弃本次。
    private func scheduleScrollToBottom(proxy: ScrollViewProxy) {
        guard !userScrolledAwayDuringStream else { return }
        scrollKickTask?.cancel()
        scrollKickTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 80_000_000)
            guard !Task.isCancelled,
                  !userScrolledAwayDuringStream,
                  let last = viewModel.messages.last else { return }
            proxy.scrollTo(last.id, anchor: .bottom)
        }
    }

    // MARK: - Empty state

    private var emptyState: some View {
        VStack(spacing: Theme.Spacing.l) {
            Image("PriceCatIcon")
                .resizable()
                .scaledToFill()
                .frame(width: 108, height: 108)
                .clipShape(RoundedRectangle(cornerRadius: 28, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 28, style: .continuous)
                        .stroke(Theme.Palette.surface, lineWidth: 2)
                )
                .themeShadow(Theme.Shadow.lifted)

            VStack(spacing: 8) {
                Text("今天想买什么？")
                    .font(Theme.Typo.display())
                    .foregroundStyle(Theme.Palette.textPrimary)
                Text("Ask anything — I'll pick from real listings, never invented.")
                    .font(Theme.Typo.caption().italic())
                    .foregroundStyle(Theme.Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, Theme.Spacing.l)
            }

            VStack(spacing: Theme.Spacing.s) {
                ForEach(starterPrompts) { prompt in
                    Button {
                        viewModel.inputText = prompt.text
                        Task { await viewModel.send() }
                    } label: {
                        HStack(spacing: Theme.Spacing.m) {
                            Image(systemName: prompt.symbolName)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(prompt.accentRole.color)
                                .frame(width: 28, height: 28)
                                .background(
                                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                                        .fill(prompt.accentRole.softFill)
                                )
                            Text(prompt.text)
                                .font(Theme.Typo.body())
                                .foregroundStyle(Theme.Palette.textPrimary)
                                .lineLimit(2)
                                .multilineTextAlignment(.leading)
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
                                .stroke(prompt.accentRole.color.opacity(0.28), lineWidth: 1)
                        )
                        .themeShadow(Theme.Shadow.card)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(prompt.text)
                }
            }
            .padding(.top, Theme.Spacing.s)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Input bar

    private var inputBar: some View {
        let isInputEmpty = viewModel.inputText.trimmingCharacters(in: .whitespaces).isEmpty
        let hasImage = viewModel.pickedImage != nil
        let canSend = !viewModel.isSending && (!isInputEmpty || hasImage)

        return VStack(spacing: Theme.Spacing.s) {
            if let notice = viewModel.uploadNotice {
                inputStatusRow(notice, systemImage: "exclamationmark.circle.fill", color: Theme.Palette.priceHot)
            }

            if let notice = viewModel.voiceNotice {
                inputStatusRow(notice, systemImage: "waveform.badge.exclamationmark", color: Theme.Palette.priceHot)
            }

            if let notice = viewModel.speechNotice {
                inputStatusRow(notice, systemImage: nil, color: Theme.Palette.textSecondary, isLoading: true)
            }

            if let picked = viewModel.pickedImage {
                pickedImageShelf(picked)
            }

            HStack(spacing: Theme.Spacing.s) {
                ImagePicker(
                    selection: $photosPickerItem,
                    picked: $viewModel.pickedImage,
                    errorMessage: $viewModel.uploadNotice
                )
                .frame(width: 38, height: 38)
                .background(
                    Circle().fill(Theme.Palette.chipSoft)
                )
                .overlay(
                    Circle().stroke(Theme.Palette.border.opacity(0.7), lineWidth: 1)
                )
                .accessibilityLabel("选择图片")

                Button {
                    if viewModel.isListening {
                        viewModel.stopVoiceInput()
                    } else {
                        inputFocused = false
                        Task { await viewModel.startVoiceInput() }
                    }
                } label: {
                    Image(systemName: viewModel.isListening ? "mic.circle.fill" : "mic.fill")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(viewModel.isListening ? .white : Theme.Palette.brand)
                        .frame(width: 38, height: 38)
                        .background(
                            Circle().fill(viewModel.isListening
                                          ? Theme.Palette.brand
                                          : Theme.Palette.chipSoft)
                        )
                        .overlay(
                            Circle().stroke(
                                viewModel.isListening ? Theme.Palette.highlight.opacity(0.45) : Theme.Palette.border.opacity(0.7),
                                lineWidth: 1
                            )
                        )
                }
                .disabled(viewModel.isSending)
                .opacity(viewModel.isSending ? 0.45 : 1)
                .accessibilityLabel(viewModel.isListening ? "停止语音输入" : "开始语音输入")

                Rectangle()
                    .fill(Theme.Palette.border.opacity(0.85))
                    .frame(width: 1, height: 24)

                TextField(
                    "",
                    text: $viewModel.inputText,
                    prompt: Text("拍图、打字或点麦克风说需求")
                        .foregroundColor(Theme.Palette.textPlaceholder),
                    axis: .vertical
                )
                .font(Theme.Typo.body())
                .foregroundStyle(Theme.Palette.textPrimary)
                .lineLimit(1...4)
                .focused($inputFocused)
                .submitLabel(.send)
                .onSubmit { Task { await viewModel.send() } }

                Button {
                    inputFocused = false
                    Task { await viewModel.send() }
                } label: {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 38, height: 38)
                        .background(
                            Circle()
                                .fill(canSend ? Theme.Palette.brand : Theme.Palette.surfaceTint)
                        )
                        .overlay(
                            Circle().stroke(canSend ? Theme.Palette.highlight.opacity(0.4) : Theme.Palette.border, lineWidth: 1)
                        )
                        .themeShadow(canSend ? Theme.Shadow.lifted
                                              : .init(color: .clear, radius: 0, x: 0, y: 0))
                }
                .disabled(!canSend)
                .opacity(canSend ? 1 : 0.7)
                .animation(.easeOut(duration: 0.15), value: canSend)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .fill(Theme.Palette.surface.opacity(0.98))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .stroke(inputFocused ? Theme.Palette.brand.opacity(0.48) : Theme.Palette.border, lineWidth: 1)
            )
            .themeShadow(Theme.Shadow.card)
        }
        .padding(.horizontal, Theme.Spacing.l)
        .padding(.top, Theme.Spacing.s)
        .padding(.bottom, 10)
        .background(
            Theme.Palette.surfaceTint
                .opacity(0.72)
                .ignoresSafeArea(edges: .bottom)
        )
        .overlay(alignment: .top) {
            Rectangle()
                .fill(Theme.Palette.border)
                .frame(height: 1)
        }
    }

    private func inputStatusRow(
        _ text: String,
        systemImage: String?,
        color: Color,
        isLoading: Bool = false
    ) -> some View {
        HStack(spacing: 7) {
            if isLoading {
                ProgressView()
                    .controlSize(.mini)
            } else if let systemImage {
                Image(systemName: systemImage)
                    .font(.system(size: 12, weight: .semibold))
            }
            Text(text)
                .font(Theme.Typo.caption(.semibold))
                .lineLimit(2)
            Spacer(minLength: 0)
        }
        .foregroundStyle(color)
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Theme.Palette.surface.opacity(0.72))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(color.opacity(0.18), lineWidth: 1)
        )
    }

    private func pickedImageShelf(_ picked: PickedImage) -> some View {
        HStack(spacing: Theme.Spacing.s) {
            if let uiimg = UIImage(contentsOfFile: picked.localURL.path) {
                Image(uiImage: uiimg)
                    .resizable()
                    .scaledToFill()
                    .frame(width: 44, height: 44)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            } else {
                Image(systemName: "photo")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(Theme.Palette.textSecondary)
                    .frame(width: 44, height: 44)
                    .background(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(Theme.Palette.chipSoft)
                    )
            }
            VStack(alignment: .leading, spacing: 2) {
                Text("已选图片")
                    .font(Theme.Typo.caption(.semibold))
                    .foregroundStyle(Theme.Palette.textPrimary)
                Text("发送时会一起上传检索")
                    .font(Theme.Typo.caption())
                    .foregroundStyle(Theme.Palette.textSecondary)
            }
            Spacer()
            Button {
                viewModel.pickedImage = nil
                photosPickerItem = nil
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(Theme.Palette.textSecondary)
                    .frame(width: 30, height: 30)
                    .background(Circle().fill(Theme.Palette.chipSoft))
            }
            .accessibilityLabel("移除图片")
        }
        .padding(8)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Theme.Palette.surface.opacity(0.82))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(Theme.Palette.border, lineWidth: 1)
        )
    }
}
