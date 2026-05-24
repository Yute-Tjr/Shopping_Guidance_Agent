import SwiftUI

/// 聊天主页：消息列表 + 底部输入栏 + 流式自动滚底。
///
/// `ChatViewModel` 在 `init` 时按全局 `AppEnvironment.baseURL` 装一个
/// `LiveChatTransport`；BaseURL 变更目前不会热重建（Phase 3 不做调试菜单，
/// 真机调试改 baseURL 后重启 App 即可）。
struct ChatView: View {
    @EnvironmentObject private var env: AppEnvironment
    @StateObject private var viewModel: ChatViewModel
    @FocusState private var inputFocused: Bool

    init(env: AppEnvironment) {
        let api = APIClient(baseURL: env.baseURL)
        let transport = LiveChatTransport(api: api)
        _viewModel = StateObject(wrappedValue: ChatViewModel(
            transport: transport,
            initialSessionID: env.sessionID
        ))
    }

    var body: some View {
        VStack(spacing: 0) {
            messageList
            Divider()
            inputBar
        }
        .navigationTitle("AI 导购")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            #if DEBUG
            // E2E smoke 钩子：simctl 启动时若传 -autoSendDemo "<query>"，自动发一次，
            // 用来在 CI / 命令行环境里验证「流式回复 + 商品卡片」整链路（参见 client/README Phase 3）。
            if let idx = CommandLine.arguments.firstIndex(of: "-autoSendDemo"),
               CommandLine.arguments.indices.contains(idx + 1) {
                let demo = CommandLine.arguments[idx + 1]
                try? await Task.sleep(nanoseconds: 400_000_000)
                viewModel.inputText = demo
                await viewModel.send()
            }
            #endif
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    viewModel.resetSession()
                } label: {
                    Image(systemName: "plus.bubble")
                }
                .accessibilityLabel("新建会话")
            }
        }
    }

    private var messageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    if viewModel.messages.isEmpty {
                        emptyState
                            .padding(.top, 60)
                    }
                    ForEach(viewModel.messages) { msg in
                        MessageBubble(message: msg) { option in
                            viewModel.inputText = option
                            Task { await viewModel.send() }
                        }
                        .id(msg.id)
                    }
                }
                .padding()
            }
            .onChange(of: viewModel.messages.last?.text) { _, _ in
                if let last = viewModel.messages.last {
                    withAnimation(.easeOut(duration: 0.15)) {
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

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "bubble.left.and.bubble.right.fill")
                .font(.system(size: 44))
                .foregroundStyle(.tint.opacity(0.6))
            Text("试试问我点什么")
                .font(.headline)
            Text("例如：推荐一款适合油皮的洗面奶")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    private var inputBar: some View {
        HStack(spacing: 8) {
            TextField("说点什么…", text: $viewModel.inputText, axis: .vertical)
                .lineLimit(1...4)
                .padding(.horizontal, 12).padding(.vertical, 8)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 18))
                .focused($inputFocused)
                .submitLabel(.send)
                .onSubmit { Task { await viewModel.send() } }

            Button {
                inputFocused = false
                Task { await viewModel.send() }
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 30))
                    .foregroundStyle(viewModel.inputText.trimmingCharacters(in: .whitespaces).isEmpty ? .gray : Color.accentColor)
            }
            .disabled(viewModel.isSending || viewModel.inputText.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
    }
}
