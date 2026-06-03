import Foundation

#if canImport(AVFoundation)
import AVFoundation
#endif

public struct SpeechVoice: Identifiable, Codable, Equatable, Sendable {
    public let id: String
    public let displayName: String
    public let locale: String
    public let gender: String

    public init(id: String, displayName: String, locale: String, gender: String) {
        self.id = id
        self.displayName = displayName
        self.locale = locale
        self.gender = gender
    }

    public static let all: [SpeechVoice] = [
        .init(id: "zh_female_vv_uranus_bigtts", displayName: "VV 女声", locale: "zh-CN", gender: "female"),
        .init(id: "saturn_zh_female_cancan_tob", displayName: "灿灿", locale: "zh-CN", gender: "female"),
        .init(id: "saturn_zh_female_keainvsheng_tob", displayName: "可爱女声", locale: "zh-CN", gender: "female"),
        .init(id: "saturn_zh_female_tiaopigongzhu_tob", displayName: "调皮公主", locale: "zh-CN", gender: "female"),
        .init(id: "saturn_zh_male_shuanglangshaonian_tob", displayName: "爽朗少年", locale: "zh-CN", gender: "male"),
        .init(id: "saturn_zh_male_tiancaitongzhuo_tob", displayName: "天才同桌", locale: "zh-CN", gender: "male"),
        .init(id: "zh_female_xiaohe_uranus_bigtts", displayName: "小荷", locale: "zh-CN", gender: "female"),
        .init(id: "zh_male_m191_uranus_bigtts", displayName: "M191 男声", locale: "zh-CN", gender: "male"),
        .init(id: "zh_male_taocheng_uranus_bigtts", displayName: "陶成", locale: "zh-CN", gender: "male"),
        .init(id: "en_male_tim_uranus_bigtts", displayName: "Tim", locale: "en-US", gender: "male"),
    ]

    public static let `default` = SpeechVoice.all[1]
}

public protocol SpeechSpeaking: Sendable {
    @MainActor
    func speak(_ text: String, voice: SpeechVoice)

    @MainActor
    func hasCachedAudio(for text: String, voice: SpeechVoice) -> Bool

    @MainActor
    func stop()
}

public extension SpeechSpeaking {
    @MainActor
    func hasCachedAudio(for text: String, voice: SpeechVoice) -> Bool {
        false
    }
}

#if canImport(AVFoundation)
public final class ServerSpeechSynthesisService: NSObject, SpeechSpeaking, @unchecked Sendable {
    private let api: AudioService
    private var audioCache: [CacheKey: Data] = [:]
    private var cacheOrder: [CacheKey] = []
    private var player: AVAudioPlayer?
    private var playbackTask: Task<Void, Never>?
    private let maxCacheItems = 8

    public init(api: AudioService) {
        self.api = api
        super.init()
    }

    @MainActor
    public func speak(_ text: String, voice: SpeechVoice) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        stop()
        let api = self.api
        let key = CacheKey(text: trimmed, voiceID: voice.id)
        if let cached = audioCache[key] {
            play(audio: cached)
            return
        }
        playbackTask = Task {
            do {
                let audio = try await api.synthesize(text: trimmed, voice: voice)
                await MainActor.run {
                    self.store(audio: audio, for: key)
                    self.play(audio: audio)
                }
            } catch {
                return
            }
        }
    }

    @MainActor
    public func hasCachedAudio(for text: String, voice: SpeechVoice) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        return audioCache[CacheKey(text: trimmed, voiceID: voice.id)] != nil
    }

    @MainActor
    public func stop() {
        playbackTask?.cancel()
        playbackTask = nil
        if player?.isPlaying == true {
            player?.stop()
        }
        player = nil
    }

    @MainActor
    private func play(audio: Data) {
        do {
            let player = try AVAudioPlayer(data: audio)
            self.player = player
            player.prepareToPlay()
            player.play()
        } catch {
            self.player = nil
        }
    }

    @MainActor
    private func store(audio: Data, for key: CacheKey) {
        audioCache[key] = audio
        cacheOrder.removeAll { $0 == key }
        cacheOrder.append(key)
        while cacheOrder.count > maxCacheItems, let oldest = cacheOrder.first {
            cacheOrder.removeFirst()
            audioCache.removeValue(forKey: oldest)
        }
    }
}

private struct CacheKey: Hashable {
    let text: String
    let voiceID: String
}
#endif
