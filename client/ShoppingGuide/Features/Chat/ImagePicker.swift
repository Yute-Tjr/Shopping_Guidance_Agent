import PhotosUI
import SwiftUI
import UIKit

/// PhotosPicker 封装 + 压缩。Phase 5 多模态入口。
///
/// 设计点：
/// - 仅相册选图；相机捕获在 demo 阶段不必要；
/// - 压缩到 ≤ 1 MB / 短边 ≤ 1600：与后端 `_MAX_BYTES` 对齐，超大图客户端先压再传；
/// - 输出 Data + 本地临时 URL（气泡渲染用，避免重复解压）。

public enum ImagePickerError: Error {
    case invalidItem
    case compressionFailed
}

public struct ImagePicker: View {
    @Binding var selection: PhotosPickerItem?
    @Binding var picked: PickedImage?
    @Binding var errorMessage: String?

    public init(
        selection: Binding<PhotosPickerItem?>,
        picked: Binding<PickedImage?>,
        errorMessage: Binding<String?>
    ) {
        self._selection = selection
        self._picked = picked
        self._errorMessage = errorMessage
    }

    public var body: some View {
        PhotosPicker(
            selection: $selection,
            matching: .images,
            photoLibrary: .shared()
        ) {
            Image(systemName: "camera")
                .resizable()
                .scaledToFit()
                .frame(width: 22, height: 22)
                .foregroundColor(Theme.Palette.brand)
        }
        .onChange(of: selection) { _, newItem in
            Task { @MainActor in
                guard let newItem else { return }
                do {
                    let result = try await Self.loadAndCompress(item: newItem)
                    picked = result
                } catch {
                    errorMessage = "图片读取失败，请重试"
                }
            }
        }
    }

    /// 静态以便 ChatView 选图逻辑（含其它入口）也能调用；同时方便单测。
    static func loadAndCompress(item: PhotosPickerItem) async throws -> PickedImage {
        guard let raw = try await item.loadTransferable(type: Data.self) else {
            throw ImagePickerError.invalidItem
        }
        guard let uiimg = UIImage(data: raw) else {
            throw ImagePickerError.invalidItem
        }
        let resized = uiimg.resizedToShortSide(maxShortSide: 1600)
        // 逐级降质量直到 ≤ 1 MB
        var quality: CGFloat = 0.85
        var data: Data? = resized.jpegData(compressionQuality: quality)
        while let d = data, d.count > 1024 * 1024, quality > 0.3 {
            quality -= 0.1
            data = resized.jpegData(compressionQuality: quality)
        }
        guard let final = data else {
            throw ImagePickerError.compressionFailed
        }
        // 写入临时目录给气泡 Image(uiImage:) 用
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(UUID().uuidString).jpg")
        try final.write(to: tmp)
        return PickedImage(data: final, localURL: tmp)
    }
}

private extension UIImage {
    func resizedToShortSide(maxShortSide: CGFloat) -> UIImage {
        let shortSide = min(size.width, size.height)
        guard shortSide > maxShortSide else { return self }
        let scale = maxShortSide / shortSide
        let newSize = CGSize(width: size.width * scale, height: size.height * scale)
        let renderer = UIGraphicsImageRenderer(size: newSize)
        return renderer.image { _ in
            self.draw(in: CGRect(origin: .zero, size: newSize))
        }
    }
}
