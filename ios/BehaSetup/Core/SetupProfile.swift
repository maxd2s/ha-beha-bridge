import Foundation

struct SetupFailure: LocalizedError, Equatable {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct Heater: Codable, Identifiable, Equatable {
    var name: String
    var deviceID: String
    var id: String { deviceID }
    enum CodingKeys: String, CodingKey { case name; case deviceID = "device_id" }
}

struct SetupProfile: Codable, Equatable {
    var ssid = ""
    var password = ""
    var brokerURI = ""
    var heaters: [Heater] = []
    enum CodingKeys: String, CodingKey { case ssid, password, heaters; case brokerURI = "broker_uri" }

    static func deviceID(_ input: String) throws -> String {
        let id = input.replacingOccurrences(of: ":", with: "").replacingOccurrences(of: "-", with: "").lowercased()
        guard id.count == 12, id.allSatisfy({ "0123456789abcdef".contains($0) }) else {
            throw SetupFailure("Enter the heater’s full 12-digit device ID (Wi-Fi MAC address).")
        }
        return id
    }

    func validated() throws -> SetupProfile {
        guard (1...32).contains(ssid.utf8.count), !ssid.contains("\0") else {
            throw SetupFailure("Wi-Fi name must contain 1–32 UTF-8 bytes.")
        }
        guard (8...63).contains(password.utf8.count), !password.contains("\0") else {
            throw SetupFailure("Wi-Fi password must contain 8–63 UTF-8 bytes.")
        }
        _ = try BrokerAddress(brokerURI)
        var result = self
        result.heaters = try heaters.map { Heater(name: $0.name, deviceID: try Self.deviceID($0.deviceID)) }
        guard Set(result.heaters.map(\.id)).count == result.heaters.count else {
            throw SetupFailure("The profile contains duplicate heater IDs.")
        }
        return result
    }

    static func decode(_ data: Data) throws -> SetupProfile {
        guard data.count < 65_536 else { throw SetupFailure("Setup file is too large.") }
        do { return try JSONDecoder().decode(Self.self, from: data).validated() }
        catch let error as SetupFailure { throw error }
        catch { throw SetupFailure("Invalid setup file. Expected ssid, password, broker_uri and heaters.") }
    }
}

struct BrokerAddress {
    let host: String
    let port: UInt16
    let tls: Bool
    let username: String?
    let password: String?

    init(_ uri: String) throws {
        guard uri.utf8.count < 199, !uri.contains(where: { $0.isWhitespace || $0 == "\0" }),
              let url = URLComponents(string: uri),
              let scheme = url.scheme, ["mqtt", "mqtts"].contains(scheme),
              let host = url.host, !host.isEmpty,
              url.path.isEmpty, url.query == nil, url.fragment == nil,
              (1...65535).contains(url.port ?? (scheme == "mqtts" ? 8883 : 1883)) else {
            throw SetupFailure("Use a broker URI under 199 bytes: mqtt://user:password@host:1883. Percent-encode special characters in credentials.")
        }
        self.host = host.hasPrefix("[") ? String(host.dropFirst().dropLast()) : host
        port = UInt16(url.port ?? (scheme == "mqtts" ? 8883 : 1883))
        tls = scheme == "mqtts"
        username = url.user
        password = url.password
    }
}
