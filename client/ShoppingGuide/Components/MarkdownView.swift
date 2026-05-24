import SwiftUI

/// 渲染 LLM 输出的轻量 markdown：段落 + 表格 + 行内格式。
///
/// 设计：
/// - 表格走 Coupert + editorial 杂志风：表头 small-caps 衬线 / 浅橙底，
///   单元格之间用 0.5pt hairline 分隔（不画外框），整体克制；
/// - 段落用 AttributedString 渲染保留行内 **bold** / *italic*；
/// - 颜色 / 字号全部来自 Theme，保持单一真相源。
struct MarkdownView: View {
    let text: String
    var textColor: Color = Theme.Palette.textPrimary

    private var blocks: [MarkdownBlock] { MarkdownParser.parse(text) }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.m) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                switch block {
                case .paragraph(let attr):
                    Text(attr)
                        .font(Theme.Typo.body())
                        .foregroundStyle(textColor)
                        .fixedSize(horizontal: false, vertical: true)
                case .table(let headers, let rows):
                    TableBlockView(headers: headers, rows: rows)
                }
            }
        }
    }
}

// MARK: - Table block

private struct TableBlockView: View {
    let headers: [String]
    let rows: [[String]]

    var body: some View {
        VStack(spacing: 0) {
            headerRow
            ForEach(Array(rows.enumerated()), id: \.offset) { idx, row in
                if idx > 0 {
                    Rectangle()
                        .fill(Theme.Palette.border)
                        .frame(height: 0.5)
                }
                bodyRow(row)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                .fill(Theme.Palette.surface)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                .stroke(Theme.Palette.border, lineWidth: 0.5)
        )
    }

    private var headerRow: some View {
        HStack(spacing: 0) {
            ForEach(Array(headers.enumerated()), id: \.offset) { i, h in
                Text(h)
                    .font(Theme.Typo.tableHeader)
                    .foregroundStyle(Theme.Palette.brand)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                if i < headers.count - 1 {
                    Rectangle().fill(Theme.Palette.border).frame(width: 0.5)
                }
            }
        }
        .background(Theme.Palette.chipSoft)
        .clipShape(
            RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
        )
        .padding(.bottom, 0)
    }

    private func bodyRow(_ cells: [String]) -> some View {
        HStack(spacing: 0) {
            ForEach(Array(cells.enumerated()), id: \.offset) { i, c in
                Text(c)
                    .font(Theme.Typo.tableCell)
                    .foregroundStyle(Theme.Palette.textPrimary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                if i < cells.count - 1 {
                    Rectangle().fill(Theme.Palette.border).frame(width: 0.5)
                }
            }
        }
    }
}
