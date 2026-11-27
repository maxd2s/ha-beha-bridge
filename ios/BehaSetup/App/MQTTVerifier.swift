import Foundation
import Network

@MainActor final class MQTTVerifier {
    var diagnostic: ((String, [String: String], Error?) -> Void)?
    private var connection: NWConnection?
    private var generation = UUID()
    private var buffer = Data()
    private var ready: CheckedContinuation<Void, Error>?
    private var deadline: Task<Void, Never>?
    private var keepalive: Task<Void, Never>?
    private var deviceID = ""
    private(set) var latest: HeaterReading?
    private(set) var receivedAt: Date?
    private(set) var failure: SetupFailure?
    private var subscribed = false

    func start(profile: SetupProfile, deviceID: String) async throws {
        stop()
        let broker = try BrokerAddress(profile.brokerURI)
        self.deviceID = try SetupProfile.deviceID(deviceID)
        diagnostic?("mqtt_preflight_started", [:], nil)
        let connect = try MQTTWire.connect(broker, deviceID: deviceID, clientID: "beha-" + String(UUID().uuidString.prefix(18)))
        let parameters = broker.tls ? NWParameters.tls : NWParameters.tcp
        parameters.prohibitedInterfaceTypes = [.cellular]
        let connection = NWConnection(host: NWEndpoint.Host(broker.host), port: NWEndpoint.Port(rawValue: broker.port)!, using: parameters)
        self.connection = connection
        let token = generation
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                ready = continuation
                deadline = Task { [weak self] in
                    try? await Task.sleep(nanoseconds: 25_000_000_000)
                    guard !Task.isCancelled, let self, self.generation == token else { return }
                    self.fail("MQTT connection timed out. Join your home Wi-Fi and allow Local Network access in iPhone Settings.")
                }
                connection.stateUpdateHandler = { [weak self] state in
                    Task { @MainActor in
                    guard let self, self.generation == token else { return }
                    switch state {
                    case .ready: self.send(connect); self.receive(token)
                    case .failed(let error):
                        self.diagnostic?("mqtt_connection_failed", [:], error)
                        self.fail("Cannot reach the MQTT broker. Check its address, port, TLS settings and iPhone Local Network permission.")
                    default: break
                    }
                    }
                }
                connection.start(queue: .main)
            }
        } onCancel: { Task { @MainActor [weak self] in self?.stop() } }
    }
    func discardOldReadings() { latest = nil; receivedAt = nil }
    func stop() {
        generation = UUID(); connection?.cancel(); connection = nil
        deadline?.cancel(); keepalive?.cancel(); deadline = nil; keepalive = nil
        ready?.resume(throwing: CancellationError()); ready = nil
        buffer = Data(); subscribed = false; failure = nil; discardOldReadings()
    }
    private func fail(_ message: String) {
        diagnostic?("mqtt_check_failed", [:], nil)
        let error = SetupFailure(message); failure = error
        deadline?.cancel(); keepalive?.cancel()
        ready?.resume(throwing: error); ready = nil
        connection?.cancel(); connection = nil
    }
    private func send(_ data: Data) {
        let token = generation
        connection?.send(content: data, completion: .contentProcessed { [weak self] error in
            Task { @MainActor in
            guard let self, self.generation == token, error != nil else { return }
            self.diagnostic?("mqtt_send_failed", [:], error)
            self.fail("MQTT connection was interrupted. Check the broker and Wi-Fi.")
            }
        })
    }
    private func receive(_ token: UUID) {
        connection?.receive(minimumIncompleteLength: 1, maximumLength: 32_768) { [weak self] data, _, ended, error in
            Task { @MainActor in
            guard let self, self.generation == token else { return }
            if let data {
                self.buffer.append(data)
                do {
                    while let (header, body) = try MQTTWire.pop(&self.buffer) { try self.handle(header, body) }
                } catch { self.fail("Invalid MQTT response from the broker."); return }
            }
            if ended || error != nil {
                self.diagnostic?("mqtt_receive_closed", [:], error)
                self.fail("MQTT connection closed. Check the broker and credentials."); return
            }
            self.receive(token)
            }
        }
    }
    private func handle(_ header: UInt8, _ body: Data) throws {
        switch header >> 4 {
        case 2:
            guard body == Data([0, 0]) else { fail("MQTT login was rejected. Check the broker username and password."); return }
            diagnostic?("mqtt_login_accepted", [:], nil)
            send(try MQTTWire.subscribe(deviceID))
        case 9:
            guard body.count == 3, body.prefix(2) == Data([0, 1]), body.last == 0 else {
                fail("Broker denied the heater telemetry subscription. Check MQTT topic permissions."); return
            }
            subscribed = true; deadline?.cancel(); ready?.resume(); ready = nil
            diagnostic?("mqtt_subscription_accepted", [:], nil)
            keepalive = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(nanoseconds: 20_000_000_000)
                    guard !Task.isCancelled else { return }; self?.send(Data([0xc0, 0]))
                }
            }
        case 3:
            if subscribed, let reading = MQTTWire.reading(header: header, body: body, deviceID: deviceID) {
                diagnostic?("fresh_heater_telemetry", [:], nil)
                latest = reading; receivedAt = Date()
            }
        default: break
        }
    }
}
