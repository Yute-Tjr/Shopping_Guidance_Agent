import PhotosUI
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

    /// 全局只跑一次的 e2e demo flag。SwiftUI `.task` 在 ChatView 每次重新出现时都
    /// 会重跑（如从 ProductDetailView pop 回来），不锁住的话会把验收 query 反复发送。
    /// 用 static 而非 @State：@State 会随 view 重建归零，static 持续整个进程生命周期。
    private static var autoSendDemoFired = false

    /// 4 条预置 query。后续 Phase 4 可换成"近期热搜"或个性化推荐。
    private let starterPrompts: [String] = [
        "推荐一款适合油皮的洗面奶",
        "200 元以下的蓝牙耳机",
        "对比一下兰蔻和雅诗兰黛的精华",
        "送女朋友的口红选什么色号",
    ]

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
        _viewModel = StateObject(wrappedValue: ChatViewModel(
            transport: transport,
            initialSessionID: env.sessionID,
            uploadService: upload
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
                    .tracking(0.5)
                Text("a daily companion for considered buying")
                    .font(Theme.Typo.caption().italic())
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
        let hasImage = viewModel.pickedImage != nil
        let canSend = !viewModel.isSending && (!isInputEmpty || hasImage)

        return VStack(spacing: 0) {
            // Phase 5：上传错误/降级提示条
            if let notice = viewModel.uploadNotice {
                Text(notice)
                    .font(Theme.Typo.caption())
                    .foregroundStyle(Theme.Palette.priceHot)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, Theme.Spacing.l)
                    .padding(.top, 6)
            }

            // Phase 5：选好图但还没发送时显示的缩略图条
            if let picked = viewModel.pickedImage {
                HStack(spacing: Theme.Spacing.s) {
                    if let uiimg = UIImage(contentsOfFile: picked.localURL.path) {
                        Image(uiImage: uiimg)
                            .resizable()
                            .scaledToFill()
                            .frame(width: 56, height: 56)
                            .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.chip))
                    }
                    Text("已选图，发送时一同上传")
                        .font(Theme.Typo.caption())
                        .foregroundStyle(Theme.Palette.textSecondary)
                    Spacer()
                    Button {
                        viewModel.pickedImage = nil
                        photosPickerItem = nil
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(Theme.Palette.textSecondary)
                            .imageScale(.large)
                    }
                }
                .padding(.horizontal, Theme.Spacing.l)
                .padding(.top, 8)
            }

            HStack(spacing: Theme.Spacing.s) {
                // Phase 5：相册入口
                ImagePicker(
                    selection: $photosPickerItem,
                    picked: $viewModel.pickedImage,
                    errorMessage: $viewModel.uploadNotice,
                )
                .frame(width: 32, height: 32)

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
        }
        .background(Theme.Palette.canvas)
    }
}
