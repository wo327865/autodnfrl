// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "AutoDNF",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "autodnf", targets: ["AutoDNF"])
    ],
    targets: [
        .executableTarget(name: "AutoDNF")
    ]
)
