import Foundation
import Combine

@MainActor final class SetupModel: ObservableObject {
    @Published var profile = SetupProfile()
    @Published var expectedID = ""
    @Published var selectedName = ""
    @Published var identity: HeaterIdentity?
    @Published var busy = false
    @Published var message = "Import your setup file or enter the network settings below."
    @Published var steps: [String] = []
    @Published var verifiedReading: HeaterReading?
    @Published var needsRecovery = false
    let bluetooth = BluetoothLink()
    private let mqtt = MQTTVerifier()
    private var session: ProvisioningSession?
    private var task: Task<Void, Never>?
    private var inspectedID: String?
    private var inspectedProfile: SetupProfile?

    func importProfile(_ url: URL) {
        guard !busy else { return }
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        do {
            let profile = try SetupProfile.decode(Data(contentsOf: url))
            reset(); self.profile = profile
            if let first = profile.heaters.first { select(first) }
            message = "Setup file loaded. Credentials stay in memory and are cleared when the app closes."
        } catch { message = safeMessage(error) }
    }
    func select(_ heater: Heater) {
        reset(); selectedName = heater.name; expectedID = heater.deviceID
    }
    func reset() {
        task?.cancel(); task = nil; bluetooth.stopScan(); bluetooth.disconnect(); mqtt.stop()
        session = nil; identity = nil; inspectedID = nil; inspectedProfile = nil
        verifiedReading = nil; needsRecovery = false; steps = []; busy = false
    }
    func clearCredentials() { reset(); profile = SetupProfile(); expectedID = ""; selectedName = ""; message = "Settings cleared from memory." }
    func inspect(_ device: NearbyHeater) {
        guard !busy else { return }
        do {
            let checked = try profile.validated(), id = try SetupProfile.deviceID(expectedID)
            guard device.name == "BEHA_" + id.suffix(6).uppercased() else {
                throw SetupFailure("Choose the Bluetooth heater matching the selected device ID.")
            }
            reset(); busy = true; message = "Checking local MQTT access…"
            task = Task {
                defer { busy = false }
                do {
                    try await mqtt.start(profile: checked, deviceID: id)
                    steps.append("MQTT login and telemetry subscription accepted")
                    message = "Verifying heater over Bluetooth…"
                    try await bluetooth.connect(device.id)
                    let session = ProvisioningSession(bluetooth); self.session = session
                    let identity = try await session.inspect(expectedID: id)
                    self.identity = identity; inspectedID = id; inspectedProfile = checked
                    steps.append("Full heater ID verified")
                    message = identity.supported ? "Ready. Review the heater and Wi-Fi, then tap Migrate." : "Firmware \(identity.firmware) has not been validated. No settings written."
                } catch {
                    message = safeMessage(error); identity = nil; session = nil
                    bluetooth.disconnect(); mqtt.stop()
                }
            }
        } catch { message = safeMessage(error) }
    }
    var canApply: Bool {
        !busy && identity?.supported == true && bluetooth.connected &&
        inspectedID == (try? SetupProfile.deviceID(expectedID)) && inspectedProfile == profile
    }
    func migrate() {
        guard canApply, let session, let checked = inspectedProfile, let id = inspectedID else { return }
        busy = true; verifiedReading = nil; needsRecovery = false
        task = Task {
            defer { busy = false; bluetooth.disconnect(); mqtt.stop(); identity = nil }
            do {
                // Recheck the broker before writing, in case the user paused on the review screen.
                message = "Rechecking MQTT access…"
                try await mqtt.start(profile: checked, deviceID: id)
                mqtt.discardOldReadings()
                message = "Saving Wi-Fi and local broker settings…"
                do { try await session.apply(checked) }
                catch {
                    if Task.isCancelled { throw CancellationError() }
                    guard session.applySent, session.brokerAcknowledged else { throw error }
                    // Applying can restart BLE before the acknowledgement reaches iOS.
                    steps.append("Heater disconnected during apply; checking actual MQTT traffic")
                }
                guard session.brokerAcknowledged else { throw SetupFailure("Broker setting was not acknowledged.") }
                steps.append("Local broker setting acknowledged")
                // Discard traffic from before the apply operation, including an already migrated heater.
                mqtt.discardOldReadings()
                message = "Waiting for fresh local MQTT readings (up to 2 minutes)…"
                for tick in 0..<60 {
                    try Task.checkCancellation()
                    if let error = mqtt.failure { throw error }
                    if let reading = mqtt.latest {
                        verifiedReading = reading; needsRecovery = false
                        steps.append("Fresh, non-retained heater telemetry received")
                        message = "Local MQTT verified. Check this heater’s readings and controls in HA before blocking its internet access."
                        return
                    }
                    if tick == 2, bluetooth.connected {
                        // Do not wait repeatedly on BLE; live MQTT is the final proof.
                        do { if try await session.wifiConnected() { steps.append("Heater reports Wi-Fi connected") } }
                        catch { steps.append("Wi-Fi status unavailable over Bluetooth; still watching MQTT") }
                    }
                    try await Task.sleep(nanoseconds: 2_000_000_000)
                }
                throw SetupFailure("No fresh MQTT reading arrived. Settings may be saved, but migration is not verified. Keep internet access enabled and check Wi-Fi, broker permissions and HA.")
            } catch {
                needsRecovery = session.settingsMayHaveChanged
                message = safeMessage(error)
                if needsRecovery { message += " Settings may have changed. Check the heater before retrying; no automatic rollback or internet block was performed." }
            }
        }
    }
    func cancel() {
        task?.cancel(); bluetooth.stopScan(); bluetooth.disconnect(); mqtt.stop()
        if session?.settingsMayHaveChanged == true {
            needsRecovery = true
            message = "Stopped. Settings may have changed; check Wi-Fi and local MQTT before retrying."
        }
    }
    func backgrounded() { if busy { cancel() }; bluetooth.stopScan() }
    private func safeMessage(_ error: Error) -> String {
        if let error = error as? SetupFailure { return error.message }
        if error is CancellationError { return "Setup stopped." }
        return "Setup could not complete. Check Bluetooth, Wi-Fi and the setup file, then retry."
    }
}
