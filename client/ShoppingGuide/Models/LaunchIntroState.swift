import Foundation

public struct LaunchIntroState: Equatable, Sendable {
    private let videoAvailable: Bool
    private let reduceMotion: Bool
    private var finished: Bool

    public init(videoAvailable: Bool, reduceMotion: Bool, finished: Bool = false) {
        self.videoAvailable = videoAvailable
        self.reduceMotion = reduceMotion
        self.finished = finished
    }

    public var shouldShowIntro: Bool {
        videoAvailable && !reduceMotion && !finished
    }

    public mutating func finish() {
        finished = true
    }
}
