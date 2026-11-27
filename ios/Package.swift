// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "BehaSetupCore",
    platforms: [.macOS(.v13), .iOS(.v17)],
    products: [.library(name: "BehaSetupCore", targets: ["BehaSetupCore"])],
    targets: [
        .target(name: "BehaSetupCore", path: "BehaSetup/Core"),
        .testTarget(name: "BehaSetupCoreTests", dependencies: ["BehaSetupCore"], path: "Tests")
    ]
)
