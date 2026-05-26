import Foundation

/// 极简 Markdown 块级解析。
///
/// 解析目标只覆盖 LLM 在我们 Prompt 约束下会产出的形态：
/// - 段落（空行分隔）
/// - GFM 表格（首行管道 + 第二行 `| --- | --- |` 分隔）
/// - 行内 **bold** / *italic* / [text](url) —— 这部分交给系统的
///   `AttributedString(markdown:)`，本 parser 只切块。
///
/// 不支持也不打算支持：标题、列表、代码块、引用、图片。LLM 真要写这些
/// 我们的 Prompt 会拒绝（docs/03 §5.2 的对比规则只允许"表格 + 总结句"）。
///
/// 失败回退：单条表格行解析失败时整段回退到 .paragraph 渲染，避免崩。
public enum MarkdownBlock: Equatable {
    case paragraph(AttributedString)
    case table(headers: [String], rows: [[String]])
}

public enum MarkdownParser {

    public static func parse(_ text: String) -> [MarkdownBlock] {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return [] }

        // 归一化换行 + PriceCat 衢线 serif 字体（New York）没有 emoji glyph，
        // emoji 会渲染为 tofu 方块。服务端 prompt 已禁用 emoji；这里客户端兜底
        // 过滤一道，防 LLM 不听 prompt 或漏网 emoji 进来。
        var normalized = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        normalized = stripEmoji(normalized)
        // strip emoji 后可能留下 "👉 阿迪..." → " 阿迪..." 这种前导空格，
        // 同时把连续多空格压回单空格，保持版式干净
        normalized = normalized
            .replacingOccurrences(of: "  ", with: " ")

        // **行级扫描**：LLM 经常把段落和表格只用单 \n 连起来（不是 GFM 标准的
        // 空行分隔），如果按 \n\n 切块就识别不到。这里改成边走边切换状态。
        let lines = normalized.split(separator: "\n", omittingEmptySubsequences: false)
            .map { String($0) }

        var out: [MarkdownBlock] = []
        var paragraphBuffer: [String] = []
        var i = 0

        func flushParagraphs() {
            // 把累积的段落行按空行切成多段
            let joined = paragraphBuffer.joined(separator: "\n")
            for part in joined.components(separatedBy: "\n\n") {
                let trimmed = part.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty {
                    out.append(.paragraph(parseInline(trimmed)))
                }
            }
            paragraphBuffer.removeAll()
        }

        while i < lines.count {
            let line = lines[i]
            // 检测表格起点：当前是表格行 + 下一行也是表格行（且列数一致 / 或下一行是分隔符）
            if isTableRow(line) && i + 1 < lines.count {
                let next = lines[i + 1]
                let isStrict = isSeparatorRow(next)
                let headerCells = splitTableRow(line)
                let nextCells = splitTableRow(next)
                let sameWidth = !isStrict && isTableRow(next)
                    && headerCells.count > 0 && headerCells.count == nextCells.count
                if isStrict || sameWidth {
                    // 收集连续表格行（含 header / separator / data）
                    flushParagraphs()
                    var tableLines: [String] = [line]
                    var j = i + 1
                    while j < lines.count, isTableRow(lines[j]) {
                        tableLines.append(lines[j])
                        j += 1
                    }
                    if let block = parseTable(tableLines.joined(separator: "\n")) {
                        out.append(block)
                    } else {
                        // 极端 fallback：当段落
                        paragraphBuffer.append(contentsOf: tableLines)
                    }
                    i = j
                    continue
                }
            }
            paragraphBuffer.append(line)
            i += 1
        }
        flushParagraphs()
        return out
    }

    // MARK: - Table

    private static func parseTable(_ raw: String) -> MarkdownBlock? {
        let lines = raw.split(separator: "\n", omittingEmptySubsequences: false).map { String($0) }
        guard lines.count >= 2 else { return nil }
        // 第 0 行必须看起来像表格行（包含 |）
        guard isTableRow(lines[0]) else { return nil }

        // 优先严格模式：第 1 行是 `| --- | --- |` 分隔行（GFM 标准）
        let strictMode = isSeparatorRow(lines[1])

        // 宽松模式：LLM 经常忘写分隔行。只要后续至少还有 1 行同样是表格行
        // 且列数一致，就当表格处理（header = 第 0 行）。
        if !strictMode {
            guard isTableRow(lines[1]) else { return nil }
            let h = splitTableRow(lines[0]).count
            let r = splitTableRow(lines[1]).count
            guard h > 0 && h == r else { return nil }
        }

        let headers = splitTableRow(lines[0])
        let columnCount = headers.count
        guard columnCount > 0 else { return nil }

        let dataStartIdx = strictMode ? 2 : 1
        var rows: [[String]] = []
        for line in lines.dropFirst(dataStartIdx) {
            guard isTableRow(line) else { continue }
            // 宽松模式下要排除"误把另一段管道行当数据"的情况，但列数不匹配也保留补齐
            var cells = splitTableRow(line)
            if cells.count < columnCount {
                cells.append(contentsOf: Array(repeating: "", count: columnCount - cells.count))
            } else if cells.count > columnCount {
                cells = Array(cells.prefix(columnCount))
            }
            rows.append(cells)
        }
        return .table(headers: headers, rows: rows)
    }

    private static func isTableRow(_ s: String) -> Bool {
        let t = s.trimmingCharacters(in: .whitespaces)
        return t.contains("|")
    }

    private static func isSeparatorRow(_ s: String) -> Bool {
        let t = s.trimmingCharacters(in: .whitespaces)
        guard t.contains("|"), t.contains("-") else { return false }
        // 每个 cell 必须形如 :?-+:? 之类
        let cells = splitTableRow(t)
        guard !cells.isEmpty else { return false }
        for c in cells {
            let stripped = c.trimmingCharacters(in: .whitespaces)
            // 允许 ---, :---, ---:, :---:
            if stripped.isEmpty { return false }
            for ch in stripped {
                if ch != "-" && ch != ":" { return false }
            }
            if !stripped.contains("-") { return false }
        }
        return true
    }

    private static func splitTableRow(_ s: String) -> [String] {
        var t = s.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|") { t.removeFirst() }
        if t.hasSuffix("|") { t.removeLast() }
        return t.split(separator: "|", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespaces) }
    }

    // MARK: - Emoji strip

    /// 过滤 emoji 字符（含主 emoji + ZWJ 连接符 + 肤色修饰 + Variation Selector），
    /// 同时也带过空格但跳过常见非 emoji 的特殊符号（如 © ® ™ § 这些 PriceCat 编辑文风
    /// 可能用到的）。
    ///
    /// 判定：grapheme cluster 含任意 emoji presentation scalar 或落在 emoji block 内即去掉。
    /// 这里采用「严过滤」——文风上 PriceCat 本就不该出 emoji，宁可多去几个也别留下方块。
    static func stripEmoji(_ s: String) -> String {
        var out = ""
        out.reserveCapacity(s.count)
        for ch in s {
            if isEmojiCluster(ch) { continue }
            out.append(ch)
        }
        return out
    }

    private static func isEmojiCluster(_ ch: Character) -> Bool {
        for scalar in ch.unicodeScalars {
            let v = scalar.value
            // Emoji presentation：默认就是 emoji 显示（如 👉 ✅）
            if scalar.properties.isEmojiPresentation { return true }
            // U+1F000+ 是 Unicode emoji 主要分区
            if v >= 0x1F000 && v <= 0x1FFFF { return true }
            // U+2600-U+27BF Misc Symbols / Dingbats，含 ✅ ✨ ❤ ☑ 等
            // 仅在 isEmoji 时算（避免误伤 © ® ™ 这类商标符）
            if v >= 0x2600 && v <= 0x27BF && scalar.properties.isEmoji { return true }
            // ZWJ + Variation Selector + 肤色修饰：单独出现也无意义，一并去掉
            if v == 0x200D || v == 0xFE0E || v == 0xFE0F { return true }
            if v >= 0x1F3FB && v <= 0x1F3FF { return true }
        }
        return false
    }

    // MARK: - Inline

    private static func parseInline(_ raw: String) -> AttributedString {
        // AttributedString(markdown:) 处理 **bold** *italic* [text](url) 等行内格式。
        // 失败时直接转纯文本，绝不丢内容。
        if let attr = try? AttributedString(
            markdown: raw,
            options: AttributedString.MarkdownParsingOptions(
                interpretedSyntax: .inlineOnlyPreservingWhitespace
            )
        ) {
            return attr
        }
        return AttributedString(raw)
    }
}
