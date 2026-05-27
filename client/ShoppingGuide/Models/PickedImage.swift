import Foundation

/// 用户从相册选/拍下的一张图：原始 JPEG bytes + 落到 tmpDir 的本地 URL。
///
/// 拆到独立文件、不依赖 UIKit：让 ChatViewModel（macOS SPM 测试目标）和
/// ImagePicker（iOS UIKit/PhotosUI 只在 iOS 编）能共享同一个值类型。
public struct PickedImage: Equatable, Sendable {
    public let data: Data
    public let localURL: URL

    public init(data: Data, localURL: URL) {
        self.data = data
        self.localURL = localURL
    }
}
