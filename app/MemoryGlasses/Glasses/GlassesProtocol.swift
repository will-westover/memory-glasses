enum GlassesProtocol {
    static let serviceUUID = "6E1F0001-6D65-6D6F-7279-676C61737365"
    static let frameUUID = "6E1F0002-6D65-6D6F-7279-676C61737365"
    static let settingsUUID = "6E1F0003-6D65-6D6F-7279-676C61737365"
    static let ackUUID = "6E1F0004-6D65-6D6F-7279-676C61737365"
    static let updateUUID = "6E1F0005-6D65-6D6F-7279-676C61737365"
    static let updateStatusUUID = "6E1F0006-6D65-6D6F-7279-676C61737365"
    static let versionUUID = "6E1F0007-6D65-6D6F-7279-676C61737365"
    static let authUUID = "6E1F0008-6D65-6D6F-7279-676C61737365"

    static let frameStart: UInt8 = 1
    static let frameData: UInt8 = 2
    static let frameEnd: UInt8 = 3
    static let updateBegin: UInt8 = 1
    static let updateData: UInt8 = 2
    static let updateEnd: UInt8 = 3
    static let updateCancel: UInt8 = 4
    static let glassesLabel: UInt8 = 103
    static let phoneLabel: UInt8 = 112

    static let recordingBytes = 16
    static let updateStep = 32768
    static let nonceBytes = 16
    static let shortProofBytes = 16
    static let proofBytes = 32

    static let deviceName = "Memory Glasses"
    static let statusReady = "ready"
    static let statusGot = "got "
    static let statusDone = "done"
    static let statusError = "error"
}
