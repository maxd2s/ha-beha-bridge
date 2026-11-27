import SwiftUI
import UniformTypeIdentifiers

@main struct BehaSetupApp: App {
    @StateObject private var model = SetupModel()
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            SetupView(model: model, bluetooth: model.bluetooth)
                .onOpenURL { model.importProfile($0) }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .background { model.backgrounded() }
                }
        }
    }
}

struct SetupView: View {
    @ObservedObject var model: SetupModel
    @ObservedObject var bluetooth: BluetoothLink
    @State private var importing = false
    @State private var confirming = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Label("Move one heater at a time", systemImage: "radiator")
                        .font(.headline)
                    Text("Keep your existing Wi-Fi. This helper connects the heater directly to your local MQTT broker for Home Assistant.")
                    Button("Import setup file", systemImage: "square.and.arrow.down") { importing = true }
                        .disabled(model.busy)
                }
                Section("1 · Network settings") {
                    TextField("Wi-Fi name", text: $model.profile.ssid)
                        .textContentType(.none).autocorrectionDisabled().textInputAutocapitalization(.never)
                    SecureField("Wi-Fi password", text: $model.profile.password).textContentType(.none)
                    SecureField("MQTT broker URI", text: $model.profile.brokerURI)
                        .textContentType(.none).autocorrectionDisabled().textInputAutocapitalization(.never)
                    Text("Broker format: mqtt://user:password@host:1883. Importing a setup file avoids typing credentials.")
                        .font(.caption).foregroundStyle(.secondary)
                }.disabled(model.busy || model.identity != nil)

                Section("2 · Choose your heater") {
                    if !model.profile.heaters.isEmpty {
                        ForEach(model.profile.heaters) { heater in
                            Button { model.select(heater) } label: {
                                HStack {
                                    VStack(alignment: .leading) {
                                        Text(heater.name)
                                        Text(heater.deviceID.uppercased()).font(.caption.monospaced()).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    if model.expectedID == heater.deviceID { Image(systemName: "checkmark.circle.fill") }
                                }
                            }.disabled(model.busy)
                        }
                    }
                    TextField("Full heater ID / Wi-Fi MAC", text: $model.expectedID)
                        .textInputAutocapitalization(.characters).autocorrectionDisabled()
                        .font(.body.monospaced()).disabled(model.busy || model.identity != nil)
                }

                Section("3 · Bluetooth setup") {
                    Text("Stand near the heater and close the BEHA app. Put only the selected heater into Gen 2 setup mode: turn down past 5° → 0 → flashing rES, keep turning until rES scrolls, then wait for Gn.2 followed by 22° and Bluetooth.")
                    Text("Resetting clears Wi-Fi settings. Have the correct network credentials ready first. If iPhone pairing fails after a reset, forget only that heater’s BEHA pairing in Bluetooth settings.")
                        .font(.caption).foregroundStyle(.secondary)
                    Text(bluetooth.status).font(.callout)
                    Button(bluetooth.scanning ? "Stop searching" : "Find nearby heaters", systemImage: "antenna.radiowaves.left.and.right") {
                        if bluetooth.scanning { bluetooth.stopScan() } else { bluetooth.scan() }
                    }.disabled(!bluetooth.available || model.busy || model.identity != nil)
                    if bluetooth.scanning { ProgressView("Searching…") }
                    ForEach(bluetooth.nearby) { heater in
                        Button {
                            model.inspect(heater)
                        } label: {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(heater.name).font(.body.monospaced())
                                    Text("Verify identity · \(heater.rssi) dBm").font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer(); Image(systemName: "chevron.right")
                            }
                        }.disabled(model.busy || model.identity != nil)
                    }
                }

                if let identity = model.identity {
                    Section("4 · Review and migrate") {
                        LabeledContent("Heater", value: model.selectedName.isEmpty ? identity.deviceID.uppercased() : model.selectedName)
                        LabeledContent("Verified ID", value: identity.deviceID.uppercased())
                        LabeledContent("Firmware", value: identity.firmware)
                        LabeledContent("Wi-Fi", value: model.profile.ssid)
                        if let broker = try? BrokerAddress(model.profile.brokerURI) {
                            LabeledContent("Local broker", value: "\(broker.host):\(broker.port)")
                        }
                        Button("Migrate verified heater", systemImage: "arrow.right.circle.fill") { confirming = true }
                            .disabled(!model.canApply)
                        if !bluetooth.connected { Text("Bluetooth disconnected. Verify the heater again before migrating.").foregroundStyle(.orange) }
                    }
                }

                Section("Progress") {
                    if model.busy { ProgressView() }
                    Text(model.message).foregroundStyle(model.needsRecovery ? .orange : .primary)
                    ForEach(Array(model.steps.enumerated()), id: \.offset) { _, step in
                        Label(step, systemImage: "checkmark.circle").font(.callout)
                    }
                    if let reading = model.verifiedReading {
                        LabeledContent("Live target", value: String(format: "%.1f °C", reading.target))
                        LabeledContent("Live room temperature", value: String(format: "%.1f °C", reading.current))
                        Text("Next: check this heater in the HA Heaters dashboard and test a target change. Only then block its internet access on your router.")
                    }
                    if model.busy {
                        Button("Stop setup", role: .destructive) { model.cancel() }
                    } else if model.identity != nil || !model.steps.isEmpty || model.needsRecovery {
                        Button("Start another check") { model.reset() }
                    }
                }

                Section("Diagnostics") {
                    Text("A report is saved automatically on this iPhone with setup steps and error codes. Passwords, broker credentials and raw payloads are excluded.")
                        .font(.caption).foregroundStyle(.secondary)
                    if model.diagnostics.saveFailed {
                        Text("The report could not be saved. Check available iPhone storage.").foregroundStyle(.orange)
                    } else {
                        ShareLink("Share diagnostic report", item: model.diagnostics.fileURL)
                            .disabled(model.busy)
                    }
                }

                Section {
                    Text("Gen 2 firmware 2.3 only. Bluetooth is needed for setup; normal control uses Wi-Fi. This app does not change your router, block internet access, or flash firmware.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Clear credentials from memory", role: .destructive) { model.clearCredentials() }
                        .disabled(model.busy)
                }
            }
            .navigationTitle("BEHA Local Setup")
            .tint(.teal)
            .fileImporter(isPresented: $importing, allowedContentTypes: [.json, UTType(importedAs: "com.beha-community.setup-profile")]) { result in
                if case let .success(url) = result { model.importProfile(url) }
            }
            .confirmationDialog("Migrate this verified heater to local MQTT?", isPresented: $confirming, titleVisibility: .visible) {
                Button("Save settings and connect") { model.migrate() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("The heater will use the displayed Wi-Fi and local broker. Keep this app open until live readings appear. Internet access will not be blocked by this app.")
            }
        }
    }
}
