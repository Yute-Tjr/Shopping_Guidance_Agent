import Foundation
import Testing
@testable import ShoppingGuideKit

@Suite("MarkdownParser")
struct MarkdownParserTests {

    // MARK: - Paragraphs

    @Test func plainSingleParagraph() {
        let blocks = MarkdownParser.parse("你好，这是一段正文。")
        #expect(blocks.count == 1)
        guard case let .paragraph(text) = blocks[0] else { Issue.record("not paragraph"); return }
        #expect(text == "你好，这是一段正文。")
    }

    @Test func multipleParagraphsSeparatedByBlankLine() {
        let blocks = MarkdownParser.parse("第一段\n\n第二段")
        #expect(blocks.count == 2)
        if case let .paragraph(a) = blocks[0] { #expect(a == "第一段") } else { Issue.record("0 not paragraph") }
        if case let .paragraph(b) = blocks[1] { #expect(b == "第二段") } else { Issue.record("1 not paragraph") }
    }

    // MARK: - Tables

    @Test func parsesSimpleTable() {
        let md = """
        | 商品 | 价格 |
        | --- | --- |
        | A | ¥99 |
        | B | ¥199 |
        """
        let blocks = MarkdownParser.parse(md)
        #expect(blocks.count == 1)
        guard case let .table(headers, rows) = blocks[0] else { Issue.record("not table"); return }
        #expect(headers == ["商品", "价格"])
        #expect(rows == [["A", "¥99"], ["B", "¥199"]])
    }

    @Test func parsesTableSurroundedByParagraphs() {
        let md = """
        以下是对比：

        | 维度 | A | B |
        | --- | --- | --- |
        | 价格 | ¥99 | ¥199 |
        | 卖点 | 控油 | 保湿 |

        综合推荐 A。
        """
        let blocks = MarkdownParser.parse(md)
        #expect(blocks.count == 3)
        if case let .paragraph(p) = blocks[0] { #expect(p == "以下是对比：") } else { Issue.record("0 not paragraph") }
        if case .table = blocks[1] {} else { Issue.record("1 not table") }
        if case let .paragraph(p) = blocks[2] { #expect(p == "综合推荐 A。") } else { Issue.record("2 not paragraph") }
    }

    @Test func tableWithLeadingColonAlignmentAccepted() {
        // markdown 表格分隔行允许 :--- / :---: / ---: 表示对齐
        let md = """
        | A | B |
        | :--- | ---: |
        | 1 | 2 |
        """
        let blocks = MarkdownParser.parse(md)
        guard case let .table(headers, rows) = blocks[0] else { Issue.record("not table"); return }
        #expect(headers == ["A", "B"])
        #expect(rows == [["1", "2"]])
    }

    @Test func missingSeparatorButPipeRowsStillTreatedAsTable() {
        // LLM 经常忘写 `| --- | --- |` 分隔行；只要管道行 ≥ 2 且列数一致，
        // parser 宽松地把第一行当 header 处理（防御性 UX，不让用户看到原始管道）。
        let md = """
        | A | B |
        | 1 | 2 |
        """
        let blocks = MarkdownParser.parse(md)
        guard case let .table(headers, rows) = blocks[0] else { Issue.record("expected table"); return }
        #expect(headers == ["A", "B"])
        #expect(rows == [["1", "2"]])
    }

    @Test func paragraphAndTableSeparatedBySingleNewline() {
        // 真实 LLM 输出场景：引导段 + \n + 表格，中间没有空行
        let md = """
        我整理了两款精华的对比：
        | 商品 | 价格 |
        | --- | --- |
        | A | ¥99 |
        总结：选 A。
        """
        let blocks = MarkdownParser.parse(md)
        #expect(blocks.count == 3)
        if case let .paragraph(p) = blocks[0] { #expect(String(p.characters) == "我整理了两款精华的对比：") } else { Issue.record("0 not paragraph") }
        if case let .table(h, r) = blocks[1] {
            #expect(h == ["商品", "价格"])
            #expect(r == [["A", "¥99"]])
        } else { Issue.record("1 not table") }
        if case let .paragraph(p) = blocks[2] { #expect(String(p.characters) == "总结：选 A。") } else { Issue.record("2 not paragraph") }
    }

    @Test func singlePipeLineStaysParagraph() {
        // 单行带管道不是表格，免得把"小贴士 | 注意事项"当表格头
        let md = "提示 | 注意事项"
        let blocks = MarkdownParser.parse(md)
        if case .table = blocks[0] { Issue.record("single line should stay paragraph") }
    }

    @Test func unclosedTableRowDoesNotCrash() {
        // 流式途中可能切到表格半行；不能崩，按段落处理即可
        let md = """
        | A | B |
        | --- | --- |
        | 1 |
        """
        let blocks = MarkdownParser.parse(md)
        guard case let .table(headers, rows) = blocks[0] else { Issue.record("not table"); return }
        #expect(headers == ["A", "B"])
        // 不完整行也尽量保留，缺位用空字符串补齐
        #expect(rows == [["1", ""]])
    }

    // MARK: - Inline formatting

    @Test func paragraphPreservesInlineMarkdown() {
        // AttributedString(markdown:) 在底层处理 **bold** *italic*；
        // 我们的 parser 只做块级切分，行内交给系统 API
        let blocks = MarkdownParser.parse("含 **粗体** 与 *斜体*")
        guard case let .paragraph(text) = blocks[0] else { Issue.record("not paragraph"); return }
        // 解析后的纯字符串应当去掉 ** 与 * 标记
        #expect(String(text.characters) == "含 粗体 与 斜体")
    }

    @Test func emptyInputReturnsNoBlocks() {
        #expect(MarkdownParser.parse("").isEmpty)
        #expect(MarkdownParser.parse("   \n\n\n   ").isEmpty)
    }
}
