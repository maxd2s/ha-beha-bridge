import Foundation
import UIKit

@MainActor final class DiagnosticStore {
    let fileURL: URL
    private var report: DiagnosticReport
    private(set) var saveFailed = false

    init() {
        fileURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("BEHA-Diagnostics.json")
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "unknown"
        report = DiagnosticReport(appVersion: "\(version) (\(build))", systemVersion: UIDevice.current.systemVersion)
        if let data = try? Data(contentsOf: fileURL), data.count < 256_000 {
            let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
            if let previous = try? decoder.decode(DiagnosticReport.self, from: data) {
                for event in previous.events.suffix(100) { report.appendPrevious(event) }
            }
        }
        record("app_started")
    }
    func record(_ event: String, _ details: [String: String] = [:], error: Error? = nil) {
        report.record(event, details: details, error: error)
        do {
            try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try report.encoded().write(to: fileURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            var url = fileURL
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            try url.setResourceValues(values)
            saveFailed = false
        } catch { saveFailed = true }
    }
}
