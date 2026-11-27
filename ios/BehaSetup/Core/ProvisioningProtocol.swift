import Foundation

enum ProtoValue: Equatable {
    case number(UInt64)
    case bytes(Data)
}

enum Wire {
    static func varint(_ input: UInt64) -> Data {
        var n = input, out = Data()
        repeat {
            let byte = UInt8(n & 127); n >>= 7
            out.append(byte | (n == 0 ? 0 : 128))
        } while n != 0
        return out
    }
    static func integer(_ field: UInt64, _ value: UInt64) -> Data { varint(field << 3) + varint(value) }
    static func message(_ field: UInt64, _ value: Data) -> Data { varint((field << 3) | 2) + varint(UInt64(value.count)) + value }
    static func parse(_ data: Data) throws -> [UInt64: ProtoValue] {
        let bytes = [UInt8](data); var index = 0
        func read() throws -> UInt64 {
            var value: UInt64 = 0
            for shift in stride(from: 0, through: 63, by: 7) {
                guard index < bytes.count else { throw SetupFailure("Truncated heater response.") }
                let byte = bytes[index]; index += 1
                guard shift != 63 || byte <= 1 else { throw SetupFailure("Invalid heater response.") }
                value |= UInt64(byte & 127) << shift
                if byte < 128 { return value }
            }
            throw SetupFailure("Invalid heater response.")
        }
        var fields: [UInt64: ProtoValue] = [:]
        while index < bytes.count {
            let tag = try read(), field = tag >> 3
            guard field > 0, fields[field] == nil else { throw SetupFailure("Invalid heater response field.") }
            switch tag & 7 {
            case 0: fields[field] = .number(try read())
            case 2:
                let length = try read()
                guard length <= UInt64(bytes.count - index) else { throw SetupFailure("Truncated heater response.") }
                fields[field] = .bytes(Data(bytes[index..<(index + Int(length))])); index += Int(length)
            default: throw SetupFailure("Unsupported heater response encoding.")
            }
        }
        return fields
    }
    static func number(_ fields: [UInt64: ProtoValue], _ key: UInt64) throws -> UInt64 {
        guard let value = fields[key] else { return 0 } // Protobuf scalar default.
        guard case let .number(n) = value else { throw SetupFailure("Unexpected heater response type.") }
        return n
    }
    static func bytes(_ fields: [UInt64: ProtoValue], _ key: UInt64) throws -> Data {
        guard case let .bytes(data)? = fields[key] else { throw SetupFailure("Missing heater response payload.") }
        return data
    }
    static let session = integer(2, 0) + message(10, message(20, Data()))
    static func config(_ kind: UInt64, _ payload: Data = Data()) -> Data {
        integer(1, kind) + message([0: UInt64(10), 2: 12, 4: 14][kind]!, payload)
    }
    static func response(_ data: Data, kind: UInt64, field: UInt64) throws -> [UInt64: ProtoValue] {
        let outer = try parse(data)
        guard try number(outer, 1) == kind else { throw SetupFailure("Unexpected Wi-Fi response.") }
        let inner = try parse(bytes(outer, field))
        guard try number(inner, 1) == 0 else { throw SetupFailure("Heater rejected the Wi-Fi operation.") }
        return inner
    }
}

@MainActor protocol ProvisioningTransport: AnyObject {
    func exchange(_ endpoint: String, _ data: Data) async throws -> Data
}

struct HeaterIdentity: Equatable {
    let deviceID: String
    let firmware: String
    var supported: Bool { firmware == "smartheater-mk2-2.3" }
}

@MainActor final class ProvisioningSession {
    private let transport: ProvisioningTransport
    private(set) var identity: HeaterIdentity?
    private(set) var settingsMayHaveChanged = false
    private(set) var brokerAcknowledged = false
    private(set) var applySent = false
    init(_ transport: ProvisioningTransport) { self.transport = transport }

    private func json(_ endpoint: String, data: Data = Data("?".utf8)) async throws -> [String: Any] {
        let reply = try await transport.exchange(endpoint, data)
        guard reply.count < 16_384,
              let object = try? JSONSerialization.jsonObject(with: reply) as? [String: Any] else {
            throw SetupFailure("Heater returned an invalid response.")
        }
        return object
    }
    func inspect(expectedID: String) async throws -> HeaterIdentity {
        identity = nil
        let expected = try SetupProfile.deviceID(expectedID)
        let proto = try await json("proto-ver")
        guard let prov = proto["prov"] as? [String: Any],
              (prov["sec_ver"] as? Int ?? 0) == 0,
              (prov["cap"] as? [String] ?? []).contains("no_sec") else {
            throw SetupFailure("Unsupported provisioning protocol. No settings written.")
        }
        let outer = try Wire.parse(await transport.exchange("prov-session", Wire.session))
        let sec = try Wire.parse(Wire.bytes(outer, 10))
        let status = try Wire.parse(Wire.bytes(sec, 21))
        guard try Wire.number(outer, 2) == 0, try Wire.number(sec, 1) == 1, try Wire.number(status, 1) == 0 else {
            throw SetupFailure("Heater did not accept the setup session.")
        }
        let device = try await json("device_id")
        guard let rawID = device["device_id"] as? String,
              (try? SetupProfile.deviceID(rawID)) == expected else {
            throw SetupFailure("The heater’s full ID does not match. No settings written.")
        }
        let version = try await json("version")
        guard let firmware = version["version"] as? String else { throw SetupFailure("Missing firmware version.") }
        let verified = HeaterIdentity(deviceID: expected, firmware: firmware)
        identity = verified
        return verified
    }
    func apply(_ profile: SetupProfile) async throws {
        let profile = try profile.validated()
        guard let identity, identity.supported else { throw SetupFailure("Verify a supported Gen 2 firmware 2.3 heater first.") }
        try Task.checkCancellation()
        settingsMayHaveChanged = true // Even an acknowledgement timeout can follow a successful write.
        let wifi = Wire.message(1, Data(profile.ssid.utf8)) + Wire.message(2, Data(profile.password.utf8))
        _ = try Wire.response(await transport.exchange("prov-config", Wire.config(2, wifi)), kind: 3, field: 13)
        try Task.checkCancellation()
        let payload = try JSONSerialization.data(withJSONObject: ["iothub_override": profile.brokerURI])
        let ack = try await json("custom_data", data: payload)
        guard ack["iothub_override"] as? String == profile.brokerURI else {
            throw SetupFailure("Broker setting was not acknowledged. Settings may have changed; check heater state before retrying.")
        }
        brokerAcknowledged = true
        try Task.checkCancellation()
        applySent = true
        _ = try Wire.response(await transport.exchange("prov-config", Wire.config(4)), kind: 5, field: 15)
    }
    func wifiConnected() async throws -> Bool {
        let status = try Wire.response(await transport.exchange("prov-config", Wire.config(0)), kind: 1, field: 11)
        switch try Wire.number(status, 2) {
        case 0: return true
        case 1, 2: return false
        case 3: throw SetupFailure("Heater could not join Wi-Fi. Check the Wi-Fi name, password and signal.")
        default: throw SetupFailure("Heater returned an unknown Wi-Fi state.")
        }
    }
}
