import AppKit
import Foundation
import Darwin

// This new app owns the daemon's macOS privacy identity. No messages or credentials
// pass through this launcher; the verified Python engine owns all channel work.
umask(0o077)
let identity = "com.christeso.sebastian.native"
if CommandLine.arguments.contains("--stop") {
    let ownURL = Bundle.main.bundleURL.standardizedFileURL
    let apps = NSRunningApplication.runningApplications(withBundleIdentifier: identity)
        .filter { $0.processIdentifier != getpid() && $0.bundleURL?.standardizedFileURL == ownURL }
    for app in apps { _ = app.terminate() }
    let deadline = Date().addingTimeInterval(45)
    while apps.contains(where: { !$0.isTerminated }) && Date() < deadline {
        RunLoop.current.run(until: Date().addingTimeInterval(0.1))
    }
    exit(apps.contains(where: { !$0.isTerminated }) ? 1 : 0)
}

final class Launcher: NSObject, NSApplicationDelegate {
    let child = Process()
    var stopping = false
    var signals: [DispatchSourceSignal] = []
    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let root = Bundle.main.object(forInfoDictionaryKey: "SebastianRoot") as? String else { exit(1) }
        child.executableURL = URL(fileURLWithPath: root + "/.venv/bin/python")
        child.arguments = ["-m", "sebastian.daemon", "--config", root + "/.private/config/installation.json"]
        var environment = ProcessInfo.processInfo.environment
        environment["SEBASTIAN_LAUNCHER_PID"] = String(getpid())
        child.environment = environment
        child.currentDirectoryURL = URL(fileURLWithPath: root)
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        child.terminationHandler = { process in
            DispatchQueue.main.async {
                // open -W exits when this app exits; launchd restarts the service.
                exit(process.terminationStatus)
            }
        }
        for number in [SIGTERM, SIGINT] {
            signal(number, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
            source.setEventHandler { NSApplication.shared.terminate(nil) }
            source.resume()
            signals.append(source)
        }
        do { try child.run() } catch { exit(1) }
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if !child.isRunning { return .terminateNow }
        if !stopping {
            stopping = true
            child.terminate()
        }
        return .terminateLater
    }
}
let app = NSApplication.shared
let delegate = Launcher()
app.delegate = delegate
app.setActivationPolicy(.prohibited)
app.run()
