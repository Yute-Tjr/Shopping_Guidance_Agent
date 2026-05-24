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

        // 1. 归一化换行
        let normalized = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")

        // 2. 切成按空行分隔的"块"
        let rawBlocks = normalized
            .components(separatedBy: "\n\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }

        var out: [MarkdownBlock] = []
        for raw in rawBlocks {
            if let table = parseTable(raw) {
                out.append(table)
            } else {
                out.append(.paragraph(parseInline(raw)))
            }
        }
        return out
    }

    // MARK: - Table

    private static func parseTable(_ raw: String) -> MarkdownBlock? {
        let lines = raw.split(separator: "\n", omittingEmptySubsequences: false).map { String($0) }
        guard lines.count >= 2 else { return nil }
        // 至少前两行：header + separator
        guard isTableRow(lines[0]) else { return nil }
        guard isSeparatorRow(lines[1]) else { return nil }

        let headers = splitTableRow(lines[0])
        let columnCount = headers.count
        guard columnCount > 0 else { return nil }

        var rows: [[String]] = []
        for line in lines.dropFirst(2) {
            guard isTableRow(line) else { continue }
            var cells = splitTableRow(line)
            // 缺位补空，多余截断 —— 流式中途的半行也能稳
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
