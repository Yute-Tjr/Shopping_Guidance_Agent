import Testing
@testable import ShoppingGuideKit

@Suite("Launch intro state")
struct LaunchIntroStateTests {
    @Test func availableVideoShowsIntroUntilFinished() {
        var state = LaunchIntroState(videoAvailable: true, reduceMotion: false)

        #expect(state.shouldShowIntro)

        state.finish()

        #expect(state.shouldShowIntro == false)
    }

    @Test func missingVideoOrReducedMotionSkipsIntro() {
        #expect(LaunchIntroState(videoAvailable: false, reduceMotion: false).shouldShowIntro == false)
        #expect(LaunchIntroState(videoAvailable: true, reduceMotion: true).shouldShowIntro == false)
    }
}
