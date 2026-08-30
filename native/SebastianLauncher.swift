import Foundation
import LocalAuthentication
import Security

let diagnosticResult = ProcessInfo.processInfo.environment["SEBASTIAN_DIAGNOSTIC_RESULT"]

func finish(_ status: Int32) -> Never {
    if let path = diagnosticResult {
        try? "\(status)\n".write(toFile: path, atomically: true, encoding: .utf8)
    }
    exit(status)
}

let home = FileManager.default.homeDirectoryForCurrentUser
let serviceHome = home.appendingPathComponent("Library/Application Support/Sebastian")
let python = serviceHome.appendingPathComponent("venv/bin/python")

func openAIKey() -> String? {
    let context = LAContext()
    context.interactionNotAllowed = true
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrAccount as String: NSUserName(),
        kSecAttrService as String: "com.christeso.sebastian.openai",
        kSecReturnData as String: true,
        kSecMatchLimit as String: kSecMatchLimitOne,
        kSecUseAuthenticationContext as String: context,
    ]
    var result: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
          let data = result as? Data,
          let value = String(data: data, encoding: .utf8),
          !value.isEmpty else {
        return nil
    }
    return value
}

guard FileManager.default.isExecutableFile(atPath: python.path) else {
    FileHandle.standardError.write(Data("Sebastian runtime is not installed.\n".utf8))
    finish(78)
}

let child = Process()
child.executableURL = python
child.arguments = ["-m", "sebastian.service"] + Array(CommandLine.arguments.dropFirst())
var environment = ProcessInfo.processInfo.environment
environment["SEBASTIAN_HOME"] = serviceHome.path
if let key = openAIKey() {
    environment["SEBASTIAN_OPENAI_API_KEY"] = key
}
child.environment = environment
child.currentDirectoryURL = serviceHome
child.standardOutput = FileHandle.standardOutput
child.standardError = FileHandle.standardError

signal(SIGTERM, SIG_IGN)
signal(SIGINT, SIG_IGN)
let signalQueue = DispatchQueue(label: "com.christeso.sebastian.signals")
let termSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: signalQueue)
let intSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: signalQueue)
termSource.setEventHandler { if child.isRunning { child.terminate() } }
intSource.setEventHandler { if child.isRunning { child.interrupt() } }
termSource.resume()
intSource.resume()

do {
    try child.run()
    child.waitUntilExit()
    finish(child.terminationStatus)
} catch {
    FileHandle.standardError.write(Data("Sebastian runtime failed to launch.\n".utf8))
    finish(70)
}
