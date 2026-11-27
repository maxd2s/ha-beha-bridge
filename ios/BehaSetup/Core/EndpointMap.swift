import Foundation

/// Resolve the advertised ESP-IDF endpoint names, never guessed UUID offsets.
struct EndpointMap {
    static let required: Set<String> = ["prov-session", "prov-config", "proto-ver", "version", "device_id", "custom_data"]
    private(set) var entries: [String: UUID] = [:]

    mutating func add(description: Data, uuid: UUID) throws {
        guard let raw = String(data: description, encoding: .utf8) else { return }
        let name = raw.trimmingCharacters(in: CharacterSet(charactersIn: "\0"))
        guard Self.required.contains(name) else { return }
        guard entries[name] == nil || entries[name] == uuid,
              !entries.contains(where: { $0.key != name && $0.value == uuid }) else {
            throw SetupFailure("Heater advertises ambiguous provisioning endpoints. No settings written.")
        }
        entries[name] = uuid
    }
    func resolved() throws -> [String: UUID] {
        guard Set(entries.keys) == Self.required else {
            let missing = Self.required.subtracting(entries.keys).sorted().joined(separator: ", ")
            throw SetupFailure("Heater did not identify these provisioning endpoints: \(missing). No settings written.")
        }
        return entries
    }
}
