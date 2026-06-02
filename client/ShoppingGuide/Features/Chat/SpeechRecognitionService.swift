import Foundation

#if canImport(AVFoundation) && canImport(Speech) && os(iOS)
import AVFoundation
import Speech
#endif

public enum SpeechRecognitionError: Error, LocalizedError, Equatable, Sendable {
    case permissionDenied
    case microphoneDenied
    case unavailable
    case recognitionFailed(message: String)

    public var errorDescription: String? {
        switch self {
        case .permissionDenied:
            return "语音识别权限未开启"
        case .microphoneDenied:
            return "麦克风权限未开启"
        case .unavailable:
            return "语音输入暂不可用"
        case .recognitionFailed(let message):
            return message
        }
    }
}

public protocol SpeechRecognizing: Sendable {
    @MainActor
    func start(
        onPartialResult: @escaping @MainActor @Sendable (String) -> Void,
        onCompletion: @escaping @MainActor @Sendable (SpeechRecognitionError?) -> Void
    ) async throws

    @MainActor
    func stop()
}

#if canImport(AVFoundation) && canImport(Speech) && os(iOS)
public final class ServerSpeechRecognitionService: NSObject, SpeechRecognizing, @unchecked Sendable {
    private let api: AudioService
    private let fallback: SpeechRecognizing?
    private let audioEngine = AVAudioEngine()
    private let lock = NSLock()
    private var converter: AVAudioConverter?
    private var targetFormat: AVAudioFormat?
    private var pcmData = Data()
    private var uploadTask: Task<Void, Never>?
    private var onPartialResult: (@MainActor @Sendable (String) -> Void)?
    private var onCompletion: (@MainActor @Sendable (SpeechRecognitionError?) -> Void)?

    public init(api: AudioService, fallback: SpeechRecognizing? = nil) {
        self.api = api
        self.fallback = fallback
        super.init()
    }

    @MainActor
    public func start(
        onPartialResult: @escaping @MainActor @Sendable (String) -> Void,
        onCompletion: @escaping @MainActor @Sendable (SpeechRecognitionError?) -> Void
    ) async throws {
        stop()
        uploadTask?.cancel()
        uploadTask = nil
        self.onPartialResult = onPartialResult
        self.onCompletion = onCompletion
        do {
            try await requestMicrophonePermission()
            try startRecording()
        } catch {
            if let fallback {
                try await fallback.start(onPartialResult: onPartialResult, onCompletion: onCompletion)
            } else {
                throw error
            }
        }
    }

    @MainActor
    public func stop() {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            uploadCurrentRecording()
            return
        }
        fallback?.stop()
    }

    @MainActor
    private func startRecording() throws {
        lock.lock()
        pcmData.removeAll(keepingCapacity: true)
        lock.unlock()

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: [.duckOthers])
        try session.setActive(true, options: .notifyOthersOnDeactivation)

        let input = audioEngine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        let target = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16_000,
            channels: 1,
            interleaved: false
        )!
        guard let converter = AVAudioConverter(from: inputFormat, to: target) else {
            throw SpeechRecognitionError.unavailable
        }
        self.converter = converter
        self.targetFormat = target

        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 2048, format: inputFormat) { [weak self] buffer, _ in
            self?.appendConvertedPCM(buffer)
        }

        audioEngine.prepare()
        try audioEngine.start()
    }

    private func appendConvertedPCM(_ buffer: AVAudioPCMBuffer) {
        guard let converter, let targetFormat else { return }
        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 32
        guard let converted = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else { return }
        var didProvideInput = false
        var convertError: NSError?
        converter.convert(to: converted, error: &convertError) { _, status in
            if didProvideInput {
                status.pointee = .noDataNow
                return nil
            }
            didProvideInput = true
            status.pointee = .haveData
            return buffer
        }
        guard convertError == nil,
              converted.frameLength > 0,
              let channel = converted.int16ChannelData?[0] else { return }
        let samples = UnsafeBufferPointer(start: channel, count: Int(converted.frameLength))
        let data = Data(buffer: samples)
        lock.lock()
        pcmData.append(data)
        lock.unlock()
    }

    @MainActor
    private func uploadCurrentRecording() {
        uploadTask?.cancel()
        lock.lock()
        let audio = pcmData
        pcmData.removeAll(keepingCapacity: true)
        lock.unlock()
        guard !audio.isEmpty else {
            onCompletion?(.recognitionFailed(message: "没有录到语音"))
            return
        }
        let api = self.api
        let onPartialResult = self.onPartialResult
        let onCompletion = self.onCompletion
        uploadTask = Task {
            do {
                let text = try await api.transcribe(pcm: audio)
                await MainActor.run {
                    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !trimmed.isEmpty {
                        onPartialResult?(text)
                    }
                    onCompletion?(nil)
                }
            } catch {
                await MainActor.run {
                    onCompletion?(.recognitionFailed(message: error.localizedDescription))
                }
            }
        }
    }

    private func requestMicrophonePermission() async throws {
        let micGranted = await withCheckedContinuation { continuation in
            if #available(iOS 17.0, *) {
                AVAudioApplication.requestRecordPermission { granted in
                    continuation.resume(returning: granted)
                }
            } else {
                AVAudioSession.sharedInstance().requestRecordPermission { granted in
                    continuation.resume(returning: granted)
                }
            }
        }
        guard micGranted else {
            throw SpeechRecognitionError.microphoneDenied
        }
    }
}

public final class SpeechRecognitionService: NSObject, SpeechRecognizing, @unchecked Sendable {
    private let recognizer: SFSpeechRecognizer?
    private let audioEngine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    public override init() {
        self.recognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))
        super.init()
    }

    @MainActor
    public func start(
        onPartialResult: @escaping @MainActor @Sendable (String) -> Void,
        onCompletion: @escaping @MainActor @Sendable (SpeechRecognitionError?) -> Void
    ) async throws {
        stop()
        try await requestPermissions()
        guard let recognizer, recognizer.isAvailable else {
            throw SpeechRecognitionError.unavailable
        }

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: [.duckOthers])
        try session.setActive(true, options: .notifyOthersOnDeactivation)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        self.request = request

        let input = audioEngine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }

        audioEngine.prepare()
        try audioEngine.start()

        task = recognizer.recognitionTask(with: request) { result, error in
            if let result {
                let text = result.bestTranscription.formattedString
                Task { @MainActor in onPartialResult(text) }
            }
            if let error {
                Task { @MainActor in
                    self.stop()
                    onCompletion(.recognitionFailed(message: error.localizedDescription))
                }
            } else if result?.isFinal == true {
                Task { @MainActor in
                    self.stop()
                    onCompletion(nil)
                }
            }
        }
    }

    @MainActor
    public func stop() {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        request?.endAudio()
        request = nil
        task?.cancel()
        task = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func requestPermissions() async throws {
        let speechStatus = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }
        guard speechStatus == .authorized else {
            throw SpeechRecognitionError.permissionDenied
        }

        let micGranted = await withCheckedContinuation { continuation in
            if #available(iOS 17.0, *) {
                AVAudioApplication.requestRecordPermission { granted in
                    continuation.resume(returning: granted)
                }
            } else {
                AVAudioSession.sharedInstance().requestRecordPermission { granted in
                    continuation.resume(returning: granted)
                }
            }
        }
        guard micGranted else {
            throw SpeechRecognitionError.microphoneDenied
        }
    }
}
#endif
