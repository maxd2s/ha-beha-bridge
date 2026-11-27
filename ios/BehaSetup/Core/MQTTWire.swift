import Foundation
import CoreFoundation

struct HeaterReading: Equatable {
    let target: Double
    let current: Double
}

enum MQTTWire {
    static func string(_ value: String) throws -> Data {
        let data = Data(value.utf8)
        guard data.count <= 65_535, !value.contains("\0") else { throw SetupFailure("MQTT string is invalid.") }
        return Data([UInt8(data.count >> 8), UInt8(data.count & 255)]) + data
    }
    static func packet(_ header: UInt8, _ body: Data) -> Data {
        var n = body.count, length = Data()
        repeat { let digit = UInt8(n % 128); n /= 128; length.append(digit | (n > 0 ? 128 : 0)) } while n > 0
        return Data([header]) + length + body
    }
    static func connect(_ broker: BrokerAddress, deviceID: String, clientID: String) throws -> Data {
        // Firmware 2.3 uses MAC-derived credentials unless URI userinfo overrides them.
        let user = broker.username ?? deviceID, password = broker.password ?? deviceID
        let body = try string("MQTT") + Data([4, 0xc2, 0, 60]) + string(clientID) + string(user) + string(password)
        return packet(0x10, body)
    }
    static func subscribe(_ id: String) throws -> Data {
        let id = try SetupProfile.deviceID(id)
        return try packet(0x82, Data([0, 1]) + string("devices/\(id)/messages/events/#") + Data([0]))
    }
    static func pop(_ buffer: inout Data) throws -> (UInt8, Data)? {
        let bytes = [UInt8](buffer)
        guard bytes.count >= 2 else { return nil }
        var length = 0, multiplier = 1
        for i in 1...4 {
            guard i < bytes.count else { return nil }
            let byte = bytes[i]; length += Int(byte & 127) * multiplier
            guard length <= 65_536 else { throw SetupFailure("MQTT response is too large.") }
            if byte < 128 {
                guard bytes.count >= i + 1 + length else { return nil }
                let body = Data(bytes[(i + 1)..<(i + 1 + length)])
                buffer.removeFirst(i + 1 + length)
                return (bytes[0], body)
            }
            multiplier *= 128
        }
        throw SetupFailure("Invalid MQTT response length.")
    }
    static func reading(header: UInt8, body: Data, deviceID: String) -> HeaterReading? {
        // A retained value cannot prove the heater is currently connected.
        guard header >> 4 == 3, header & 1 == 0, (header >> 1) & 3 == 0 else { return nil }
        let bytes = [UInt8](body)
        guard bytes.count >= 2 else { return nil }
        let length = Int(bytes[0]) * 256 + Int(bytes[1])
        guard bytes.count >= 2 + length,
              let topic = String(bytes: bytes[2..<(2 + length)], encoding: .utf8),
              topic.hasPrefix("devices/\(deviceID)/messages/events/") else { return nil }
        let payload = Data(bytes.dropFirst(2 + length))
        guard payload.count <= 16_384,
              let object = try? JSONSerialization.jsonObject(with: payload) as? [String: Any],
              let heater = object["com.devicedrive.heater"] as? [String: Any],
              object["com.beha.heater"] is [String: Any],
              let target = heater["target_temperature"] as? NSNumber,
              let current = heater["current_temperature"] as? NSNumber,
              CFGetTypeID(target) != CFBooleanGetTypeID(), CFGetTypeID(current) != CFBooleanGetTypeID(),
              target.doubleValue.isFinite, current.doubleValue.isFinite,
              (0...35).contains(target.doubleValue), (-40...125).contains(current.doubleValue) else { return nil }
        return HeaterReading(target: target.doubleValue, current: current.doubleValue)
    }
}
