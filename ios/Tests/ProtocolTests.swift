import XCTest
@testable import BehaSetupCore

final class ProtocolTests: XCTestCase {
    func testDiagnosticReportExcludesCredentialsAndNSErrorPayloads() throws {
        let secret = "SECRET_MUST_NOT_APPEAR"
        var report = DiagnosticReport(appVersion: "test", systemVersion: "test")
        let error = NSError(domain: "CBATTErrorDomain", code: 15, userInfo: [NSLocalizedDescriptionKey: secret, "payload": secret, NSUnderlyingErrorKey: NSError(domain: secret, code: 3)])
        report.record("ble_operation_failed", details: ["operation": "read", "endpoint": "proto-ver", "password": secret, "broker_uri": secret, "payload": secret, "ssid": secret], error: error)
        let encoded = try report.encoded()
        XCTAssertFalse(String(decoding: encoded, as: UTF8.self).contains(secret))
        XCTAssertEqual(report.events.last?.error, DiagnosticError(error))
        XCTAssertEqual(report.events.last?.details, ["operation": "read", "endpoint": "proto-ver"])
        let unknown = DiagnosticError(NSError(domain: secret, code: 99, userInfo: [NSLocalizedDescriptionKey: secret]))
        XCTAssertEqual(unknown.domain, "Other")
        XCTAssertFalse(unknown.bluetoothAdvice.contains(secret))
        XCTAssertTrue(DiagnosticError(error).bluetoothAdvice.contains("encryption"))
    }
    func testDiagnosticHistoryIsBoundedAndSurvivesSerialization() throws {
        var report = DiagnosticReport(appVersion: "0.1.1", systemVersion: "test")
        for index in 0..<300 { report.record("event_\(index)") }
        XCTAssertEqual(report.events.count, 200)
        XCTAssertEqual(report.events.first?.event, "event_100")
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        let restored = try decoder.decode(DiagnosticReport.self, from: report.encoded())
        XCTAssertEqual(restored.events.count, 200)
        XCTAssertEqual(restored.events.last?.event, "event_299")
    }
    func testWireVectorsMatchProvenPythonProvisioner() {
        XCTAssertEqual(Wire.session, Data([0x10, 0, 0x52, 3, 0xa2, 1, 0]))
        XCTAssertEqual(Wire.config(0), Data([8, 0, 0x52, 0]))
        XCTAssertEqual(Wire.config(4), Data([8, 4, 0x72, 0]))
    }
    func testMalformedProtobufCannotBeSuccess() {
        for data in [Data([0x80]), Data([0x52, 5, 0]), Data([0]), Data(repeating: 0xff, count: 12), Data([8, 0, 8, 1])] {
            XCTAssertThrowsError(try Wire.parse(data))
        }
        XCTAssertThrowsError(try Wire.response(Data([8, 3, 0x6a, 2, 8, 1]), kind: 3, field: 13))
        XCTAssertThrowsError(try Wire.response(Data([8, 5, 0x7a, 0]), kind: 3, field: 13))
        XCTAssertThrowsError(try Wire.response(Data([8, 3]), kind: 3, field: 13))
    }
    func testWiFiLimitsAreBytesNotCharacters() throws {
        var profile = validProfile
        profile.ssid = String(repeating: "é", count: 17)
        XCTAssertThrowsError(try profile.validated())
        profile.ssid = "Test"
        profile.password = String(repeating: "é", count: 32)
        XCTAssertThrowsError(try profile.validated())
    }
    func testProfileRejectsDuplicateOrMalformedIDs() {
        var profile = validProfile
        profile.heaters = [Heater(name: "One", deviceID: "AA:BB:CC:DD:EE:FF"), Heater(name: "Two", deviceID: "aabbccddeeff")]
        XCTAssertThrowsError(try profile.validated())
        XCTAssertThrowsError(try SetupProfile.deviceID("beha_eeff"))
        XCTAssertThrowsError(try SetupProfile.decode(Data("{\"password\":\"DO_NOT_LEAK\"}".utf8))) { error in
            XCTAssertFalse(error.localizedDescription.contains("DO_NOT_LEAK"))
        }
    }
    func testBrokerValidationAndCredentials() throws {
        let broker = try BrokerAddress("mqtt://user:p%40ss@192.0.2.10:1883")
        XCTAssertEqual(broker.password, "p@ss")
        XCTAssertEqual(broker.port, 1883)
        XCTAssertFalse(broker.tls)
        for uri in ["http://host", "mqtt://host:0", "mqtt://host/topic", "mqtt://host?x=1", "mqtt://host/#token", "mqtt://user:bad password@host", "mqtt://" + String(repeating: "x", count: 200)] {
            XCTAssertThrowsError(try BrokerAddress(uri))
        }
    }
    func testMQTTFramesCanArriveFragmentedOrTogether() throws {
        var buffer = Data([0x20])
        XCTAssertNil(try MQTTWire.pop(&buffer))
        buffer.append(contentsOf: [2, 0])
        XCTAssertNil(try MQTTWire.pop(&buffer))
        buffer.append(contentsOf: [0, 0xd0, 0])
        let packet = try XCTUnwrap(MQTTWire.pop(&buffer))
        XCTAssertEqual(packet.0, 0x20); XCTAssertEqual(packet.1, Data([0, 0]))
        XCTAssertEqual(try MQTTWire.pop(&buffer)?.0, 0xd0)
        XCTAssertTrue(buffer.isEmpty)
        buffer = Data([0x30, 0xff, 0xff, 0xff, 0xff])
        XCTAssertThrowsError(try MQTTWire.pop(&buffer))
    }
    func testOnlyFreshCorrectHeaterTelemetryProvesSuccess() throws {
        let topic = try MQTTWire.string("devices/aabbccddeeff/messages/events/private-topic")
        let payload = Data(#"{"com.devicedrive.heater":{"target_temperature":20.5,"current_temperature":21.0},"com.beha.heater":{"heater_mode":3}}"#.utf8)
        let body = topic + payload
        XCTAssertEqual(MQTTWire.reading(header: 0x30, body: body, deviceID: "aabbccddeeff"), HeaterReading(target: 20.5, current: 21))
        XCTAssertNil(MQTTWire.reading(header: 0x31, body: body, deviceID: "aabbccddeeff")) // retained
        XCTAssertNil(MQTTWire.reading(header: 0x30, body: body, deviceID: "112233445566"))
        let invalid = Data(#"{"com.devicedrive.heater":{"target_temperature":true,"current_temperature":21},"com.beha.heater":{}}"#.utf8)
        XCTAssertNil(MQTTWire.reading(header: 0x30, body: topic + invalid, deviceID: "aabbccddeeff"))
    }
    func testMQTTSubscribeNeverPublishesControl() throws {
        var buffer = try MQTTWire.subscribe("aabbccddeeff")
        let packet = try XCTUnwrap(MQTTWire.pop(&buffer))
        XCTAssertEqual(packet.0, 0x82)
        XCTAssertEqual(packet.1.suffix(1), Data([0]))
    }

    @MainActor func testIdentityMismatchStopsBeforeSettingsWrites() async throws {
        let transport = FakeTransport(); transport.deviceID = "112233445566"
        let session = ProvisioningSession(transport)
        do { _ = try await session.inspect(expectedID: "aabbccddeeff"); XCTFail("Should reject wrong heater") } catch {}
        XCTAssertNil(session.identity)
        XCTAssertFalse(transport.calls.contains { ["custom_data", "prov-config"].contains($0.0) })
    }
    @MainActor func testUnsupportedFirmwareCannotApply() async throws {
        let transport = FakeTransport(); transport.firmware = "smartheater-mk2-9.9"
        let session = ProvisioningSession(transport)
        _ = try await session.inspect(expectedID: "aabbccddeeff")
        do { try await session.apply(validProfile); XCTFail("Should reject unsupported firmware") } catch {}
        XCTAssertFalse(session.settingsMayHaveChanged)
    }
    @MainActor func testBrokerAckMismatchDoesNotApplyWiFi() async throws {
        let transport = FakeTransport(); transport.rejectBroker = true
        let session = ProvisioningSession(transport)
        _ = try await session.inspect(expectedID: "aabbccddeeff")
        do { try await session.apply(validProfile); XCTFail("Should reject missing broker acknowledgement") } catch {}
        XCTAssertTrue(session.settingsMayHaveChanged)
        XCTAssertFalse(session.applySent)
        XCTAssertFalse(session.brokerAcknowledged)
        XCTAssertFalse(transport.calls.contains { $0.0 == "prov-config" && $0.1 == Wire.config(4) })
    }
    @MainActor func testSuccessfulSequenceAndWiFiStatus() async throws {
        let transport = FakeTransport(), session = ProvisioningSession(FakeTransport())
        do { try await session.apply(validProfile); XCTFail("Unverified apply must fail") } catch {}
        let verifiedSession = ProvisioningSession(transport)
        _ = try await verifiedSession.inspect(expectedID: "AA:BB:CC:DD:EE:FF")
        try await verifiedSession.apply(validProfile)
        XCTAssertTrue(verifiedSession.brokerAcknowledged)
        XCTAssertTrue(verifiedSession.applySent)
        let connected = try await verifiedSession.wifiConnected()
        XCTAssertTrue(connected)
        XCTAssertEqual(transport.calls.map(\.0), ["proto-ver", "prov-session", "device_id", "version", "prov-config", "custom_data", "prov-config", "prov-config"])
        let configured = try Wire.parse(Wire.bytes(Wire.parse(transport.calls[4].1), 12))
        XCTAssertEqual(try Wire.bytes(configured, 1), Data(validProfile.ssid.utf8))
        transport.wifiState = 3
        do { _ = try await verifiedSession.wifiConnected(); XCTFail("Should reject Wi-Fi failure") } catch {}
    }
    @MainActor func testDisconnectDuringApplyIsUncertainNotAutomaticSuccess() async throws {
        let transport = FakeTransport(); transport.disconnectOnApply = true
        let session = ProvisioningSession(transport)
        _ = try await session.inspect(expectedID: "aabbccddeeff")
        do { try await session.apply(validProfile); XCTFail("Transport failure must propagate") } catch {}
        XCTAssertTrue(session.applySent)
        XCTAssertTrue(session.brokerAcknowledged)
        XCTAssertTrue(session.settingsMayHaveChanged)
    }
}

private let validProfile = SetupProfile(ssid: "Test WiFi", password: "fake-wifi-password", brokerURI: "mqtt://test:fake-broker-password@192.0.2.10:1883", heaters: [])

@MainActor private final class FakeTransport: ProvisioningTransport {
    var deviceID = "aabbccddeeff"
    var firmware = "smartheater-mk2-2.3"
    var rejectBroker = false
    var disconnectOnApply = false
    var wifiState: UInt64 = 0
    var calls: [(String, Data)] = []
    func exchange(_ endpoint: String, _ data: Data) async throws -> Data {
        calls.append((endpoint, data))
        switch endpoint {
        case "proto-ver": return Data(#"{"prov":{"sec_ver":0,"cap":["no_sec","wifi_scan"]}}"#.utf8)
        case "prov-session": return Wire.integer(2, 0) + Wire.message(10, Wire.integer(1, 1) + Wire.message(21, Data()))
        case "device_id": return try JSONSerialization.data(withJSONObject: ["device_id": deviceID])
        case "version": return try JSONSerialization.data(withJSONObject: ["version": firmware])
        case "custom_data": return rejectBroker ? Data("{}".utf8) : data
        case "prov-config":
            let kind = try Wire.number(Wire.parse(data), 1)
            if kind == 4 && disconnectOnApply { throw SetupFailure("Disconnected") }
            return Wire.integer(1, kind + 1) + Wire.message([0: UInt64(11), 2: 13, 4: 15][kind]!, kind == 0 ? Wire.integer(2, wifiState) : Data())
        default: throw SetupFailure("Unexpected endpoint")
        }
    }
}
