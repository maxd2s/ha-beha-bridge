import CoreBluetooth
import Combine
import Foundation

struct NearbyHeater: Identifiable {
    let id: UUID
    let name: String
    let rssi: Int
}

// CoreBluetooth delivers these delegates on the explicit main queue below.
@MainActor final class BluetoothLink: NSObject, ObservableObject, @preconcurrency CBCentralManagerDelegate, @preconcurrency CBPeripheralDelegate, ProvisioningTransport {
    @Published var nearby: [NearbyHeater] = []
    @Published var scanning = false
    @Published var available = false
    @Published var status = "Bluetooth is starting…"
    @Published var connected = false
    var diagnostic: ((String, [String: String], Error?) -> Void)?
    private var central: CBCentralManager!
    private var devices: [UUID: CBPeripheral] = [:]
    private var peer: CBPeripheral?
    private var characteristics: [String: CBCharacteristic] = [:]
    private var pending: CheckedContinuation<Data, Error>?
    private var pendingID: UUID?
    private var operation = ""
    private var endpoint = ""
    private var deadline: Task<Void, Never>?
    private var scanDeadline: Task<Void, Never>?
    private var expectedCharacteristic: CBUUID?
    private static let service = CBUUID(string: "9097f5e0-abaa-7492-6f4a-63694d6ef6a3")
    private static let userDescription = CBUUID(string: "2901")

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: .main)
    }
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        available = central.state == .poweredOn
        diagnostic?("bluetooth_state", ["bluetooth_state": String(central.state.rawValue), "authorization": String(CBCentralManager.authorization.rawValue)], nil)
        switch central.state {
        case .poweredOn: status = "Ready to find nearby heaters"
        case .poweredOff: status = "Turn on Bluetooth in iPhone Settings."
        case .unauthorized: status = "Allow Bluetooth for BEHA Local Setup in iPhone Settings."
        case .unsupported: status = "Bluetooth is unavailable on this device."
        default: status = "Bluetooth is starting…"
        }
        if !available { stopScan(); disconnect() }
    }
    func scan() {
        guard available else { return }
        stopScan(); nearby = []; devices = [:]; scanning = true
        diagnostic?("scan_started", ["bluetooth_state": String(central.state.rawValue), "authorization": String(CBCentralManager.authorization.rawValue)], nil)
        central.scanForPeripherals(withServices: nil)
        scanDeadline = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 60_000_000_000)
            guard !Task.isCancelled else { return }; self?.stopScan()
        }
    }
    func stopScan() { central.stopScan(); scanning = false; scanDeadline?.cancel(); scanDeadline = nil }
    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let name = (advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? peripheral.name ?? "").uppercased()
        guard name.hasPrefix("BEHA_"), name.count == 11 else { return }
        devices[peripheral.identifier] = peripheral
        let item = NearbyHeater(id: peripheral.identifier, name: name, rssi: RSSI.intValue)
        nearby.removeAll { $0.id == item.id }; nearby.append(item)
        nearby.sort { $0.rssi > $1.rssi }
    }
    private func wait(_ kind: String, seconds: UInt64 = 35, start: @escaping () -> Void) async throws -> Data {
        guard pending == nil else { throw SetupFailure("Another Bluetooth operation is in progress.") }
        try Task.checkCancellation()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                let id = UUID(); pendingID = id; pending = continuation; operation = kind
                diagnostic?("ble_operation_started", ["operation": kind, "endpoint": endpoint], nil)
                deadline = Task { [weak self] in
                    try? await Task.sleep(nanoseconds: seconds * 1_000_000_000)
                    guard !Task.isCancelled, let self, self.pendingID == id else { return }
                    self.diagnostic?("ble_timeout", ["operation": self.operation, "endpoint": self.endpoint], nil)
                    self.finish(.failure(SetupFailure("Bluetooth \(self.operation) timed out at \(self.endpoint.isEmpty ? "connection setup" : self.endpoint). Keep the phone near the heater. A diagnostic report was saved.")))
                    self.disconnect()
                }
                start()
            }
        } onCancel: { Task { @MainActor [weak self] in self?.disconnect() } }
    }
    private func finish(_ result: Result<Data, Error>) {
        let continuation = pending; pending = nil; pendingID = nil; operation = ""
        deadline?.cancel(); deadline = nil; continuation?.resume(with: result)
    }
    func connect(_ id: UUID) async throws {
        stopScan(); disconnect()
        guard available, let device = devices[id] else { throw SetupFailure("Scan again to find the heater.") }
        if let found = nearby.first(where: { $0.id == id }) { diagnostic?("heater_selected", ["rssi": String(found.rssi)], nil) }
        peer = device; device.delegate = self
        _ = try await wait("connect", seconds: 25) { self.central.connect(device) }
        _ = try await wait("services") { device.discoverServices([Self.service]) }
        guard let service = device.services?.first(where: { $0.uuid == Self.service }) else { throw SetupFailure("Expected BEHA service was not found.") }
        _ = try await wait("characteristics") { device.discoverCharacteristics(nil, for: service) }
        var mapping = EndpointMap()
        for characteristic in service.characteristics ?? [] {
            expectedCharacteristic = characteristic.uuid; endpoint = "descriptor_lookup"
            _ = try await wait("descriptors") { device.discoverDescriptors(for: characteristic) }
            for descriptor in characteristic.descriptors ?? [] where descriptor.uuid == Self.userDescription {
                let description = try await wait("descriptor_read") { device.readValue(for: descriptor) }
                guard let uuid = UUID(uuidString: characteristic.uuid.uuidString) else { continue }
                try mapping.add(description: description, uuid: uuid)
            }
        }
        for (name, uuid) in try mapping.resolved() {
            guard let characteristic = service.characteristics?.first(where: { $0.uuid == CBUUID(nsuuid: uuid) }),
                  characteristic.properties.contains(.read), characteristic.properties.contains(.write) else {
                throw SetupFailure("This heater’s provisioning endpoints are unsupported.")
            }
            characteristics[name] = characteristic
            diagnostic?("endpoint_mapped_from_descriptor", ["endpoint": name, "characteristic_uuid": uuid.uuidString, "properties": String(characteristic.properties.rawValue)], nil)
        }
        endpoint = ""; expectedCharacteristic = nil
    }
    func disconnect() {
        finish(.failure(CancellationError()))
        if let peer { central.cancelPeripheralConnection(peer) }
        peer = nil; connected = false; characteristics = [:]; expectedCharacteristic = nil; endpoint = ""
    }
    func prepareSecureAccess() async throws {
        guard let peer, peer.state == .connected, let characteristic = characteristics["proto-ver"] else {
            throw SetupFailure("Connect to the heater before checking Bluetooth secure access.")
        }
        // An encrypted characteristic read lets CoreBluetooth initiate pairing.
        // ESP-IDF reads return the previous response (possibly empty); discard it.
        // This probe must never be interpreted as protocol or identity verification.
        expectedCharacteristic = characteristic.uuid; endpoint = "proto-ver"
        diagnostic?("secure_access_probe_started", ["endpoint": endpoint], nil)
        _ = try await wait("pairing_read", seconds: 60) { peer.readValue(for: characteristic) }
        diagnostic?("secure_access_probe_completed", ["endpoint": endpoint], nil)
    }
    func exchange(_ endpoint: String, _ data: Data) async throws -> Data {
        guard let peer, peer.state == .connected, let characteristic = characteristics[endpoint] else {
            throw SetupFailure("Heater disconnected. Re-enter setup mode and verify it again.")
        }
        guard data.count <= peer.maximumWriteValueLength(for: .withResponse) else {
            throw SetupFailure("Bluetooth write is too large for this connection.")
        }
        expectedCharacteristic = characteristic.uuid
        self.endpoint = endpoint
        diagnostic?("ble_exchange", ["endpoint": endpoint, "byte_count": String(data.count)], nil)
        _ = try await wait("write") { peer.writeValue(data, for: characteristic, type: .withResponse) }
        return try await wait("read") { peer.readValue(for: characteristic) }
    }
    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        guard peripheral === peer, operation == "connect" else { return }
        diagnostic?("ble_connected", [:], nil)
        connected = true; finish(.success(Data()))
    }
    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        guard peripheral === peer else { return }; connected = false
        diagnostic?("ble_connect_failed", [:], error)
        finish(.failure(SetupFailure("Could not connect to the heater. Close the BEHA app and keep only this heater in setup mode.")))
    }
    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        guard peripheral === peer else { return }; connected = false
        diagnostic?("ble_disconnected", ["operation": operation, "endpoint": endpoint], error)
        finish(.failure(SetupFailure("Bluetooth disconnected. The heater may be restarting; check local MQTT before retrying.")))
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard peripheral === peer, operation == "services" else { return }; complete(error)
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard peripheral === peer, operation == "characteristics" else { return }; complete(error)
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverDescriptorsFor characteristic: CBCharacteristic, error: Error?) {
        guard peripheral === peer, operation == "descriptors", characteristic.uuid == expectedCharacteristic else { return }; complete(error)
    }
    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor descriptor: CBDescriptor, error: Error?) {
        guard peripheral === peer, operation == "descriptor_read", descriptor.characteristic?.uuid == expectedCharacteristic,
              descriptor.uuid == Self.userDescription else { return }
        if let text = descriptor.value as? String { complete(error, value: Data(text.utf8)) }
        else { complete(error, value: descriptor.value as? Data) }
    }
    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard peripheral === peer, operation == "write", characteristic.uuid == expectedCharacteristic else { return }; complete(error)
    }
    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard peripheral === peer, ["read", "pairing_read"].contains(operation), characteristic.uuid == expectedCharacteristic else { return }
        complete(error, value: characteristic.value)
    }
    private func complete(_ error: Error?, value: Data? = Data()) {
        if let error {
            diagnostic?("ble_operation_failed", ["operation": operation, "endpoint": endpoint], error)
            let safe = DiagnosticError(error)
            finish(.failure(SetupFailure("Bluetooth \(operation) failed at \(endpoint.isEmpty ? "connection setup" : endpoint) (\(safe.domain), code \(safe.code)). \(safe.bluetoothAdvice)")))
        } else if let value {
            diagnostic?("ble_operation_completed", ["operation": operation, "endpoint": endpoint, "byte_count": String(value.count)], nil)
            finish(.success(value))
        }
        else { finish(.failure(SetupFailure("Heater returned no data."))) }
    }
}
