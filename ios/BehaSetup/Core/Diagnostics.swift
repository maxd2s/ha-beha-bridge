import Foundation

/// Codes only: never retain NSError descriptions, userInfo, endpoint bytes or URIs.
struct DiagnosticError: Codable, Equatable {
    let domain: String
    let code: Int
    init(_ error: Error) {
        let error = error as NSError
        let allowed = ["CBATTErrorDomain", "CBErrorDomain", "NSPOSIXErrorDomain", "NSURLErrorDomain", "NSOSStatusErrorDomain", "kCFErrorDomainCFNetwork"]
        domain = allowed.contains(error.domain) ? error.domain : "Other"
        code = error.code
    }
    var bluetoothAdvice: String {
        if domain == "CBATTErrorDomain", [5, 8, 12, 15].contains(code) {
            return "The heater requires Bluetooth authentication or encryption. Accept any pairing prompt. If it was reset, forget only this heater’s old BEHA pairing in iPhone Settings, then retry."
        }
        if domain == "CBErrorDomain", code == 14 {
            return "The heater removed its pairing information. Forget only this heater’s BEHA pairing in iPhone Settings, then retry."
        }
        return "Keep the phone near the heater and leave the official BEHA app closed. The diagnostic report contains the failed step and error code."
    }
}

struct DiagnosticEvent: Codable {
    let time: Date
    let event: String
    let details: [String: String]
    let error: DiagnosticError?
}

struct DiagnosticReport: Codable {
    let schemaVersion: Int
    let appVersion: String
    let systemVersion: String
    let pairingState: String
    private(set) var events: [DiagnosticEvent] = []

    init(appVersion: String, systemVersion: String) {
        schemaVersion = 1; self.appVersion = appVersion; self.systemVersion = systemVersion
        pairingState = "iOS does not expose saved pairing state through public CoreBluetooth APIs. See ATT/CB error codes."
    }

    mutating func appendPrevious(_ event: DiagnosticEvent) {
        events.append(event)
        if events.count > 200 { events.removeFirst(events.count - 200) }
    }
    mutating func record(_ event: String, details: [String: String] = [:], error: Error? = nil) {
        let keys: Set<String> = ["operation", "endpoint", "characteristic_uuid", "bluetooth_state", "authorization", "properties", "byte_count", "outcome", "settings_may_have_changed", "broker_acknowledged", "apply_sent", "device_id", "rssi", "firmware_supported"]
        let safe = details.filter { keys.contains($0.key) }.mapValues { String($0.prefix(120)) }
        events.append(DiagnosticEvent(time: Date(), event: String(event.prefix(80)), details: safe, error: error.map(DiagnosticError.init)))
        if events.count > 200 { events.removeFirst(events.count - 200) }
    }
    func encoded() throws -> Data {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        return try encoder.encode(self)
    }
}
