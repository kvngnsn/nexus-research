import AppKit
import WebKit

final class Desktop: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    private var window: NSWindow!
    private var web: WKWebView!
    private var engine: Process?
    private var readyFile: URL!
    private var logHandle: FileHandle?
    private var origin: URL?
    private var polls = 0
    private var timer: Timer?
    private let files = FileManager.default
    private var dataRoot: URL {
        if let qa = Bundle.main.object(forInfoDictionaryKey: "NexusQADataDirectory") as? String {
            return URL(fileURLWithPath: qa, isDirectory: true)
        }
        if let custom = ProcessInfo.processInfo.environment["NEXUS_NATIVE_DATA_DIR"] {
            return URL(fileURLWithPath: custom, isDirectory: true)
        }
        return files.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Nexus Research", isDirectory: true)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let icon = Bundle.main.url(forResource: "Nexus", withExtension: "icns") {
            NSApp.applicationIconImage = NSImage(contentsOf: icon)
        }
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(self, name: "save")
        configuration.userContentController.add(self, name: "reveal")
        configuration.userContentController.add(self, name: "chooseFolder")
        web = WKWebView(frame: .zero, configuration: configuration)
        web.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1080, height: 850),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Nexus Research"
        window.backgroundColor = .white
        window.appearance = NSAppearance(named: .aqua)
        window.minSize = NSSize(width: 640, height: 550)
        window.contentView = web
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        let menu = NSMenu()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Nexus Research", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Nexus Research", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let item = NSMenuItem(); item.submenu = appMenu; menu.addItem(item)
        let edit = NSMenu(title: "Edit")
        for (title, action, key) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"),
                                    ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
                                    ("Select All", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        let editItem = NSMenuItem(); editItem.submenu = edit; menu.addItem(editItem)
        NSApp.mainMenu = menu
        do { try startEngine() } catch { showError("Could not start the local service. \(error.localizedDescription)") }
    }

    @objc private func showAbout() {
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Nexus Research", .applicationVersion: "0.4.0 open source",
            .credits: NSAttributedString(string: "Open-source research. Your findings, saved to your folder. MIT License."),
        ])
    }

    private func startEngine() throws {
        try files.createDirectory(at: dataRoot, withIntermediateDirectories: true,
                                  attributes: [.posixPermissions: 0o700])
        let temp = files.temporaryDirectory.appendingPathComponent("nexus-\(UUID().uuidString)")
        try files.createDirectory(at: temp, withIntermediateDirectories: true,
                                  attributes: [.posixPermissions: 0o700])
        readyFile = temp.appendingPathComponent("ready.json")
        let log = dataRoot.appendingPathComponent("desktop.log")
        if !files.fileExists(atPath: log.path) { files.createFile(atPath: log.path, contents: nil) }
        logHandle = try FileHandle(forWritingTo: log)
        try logHandle?.seekToEnd()
        let process = Process()
        process.executableURL = Bundle.main.resourceURL!.appendingPathComponent("Engine/NexusEngine")
        process.currentDirectoryURL = dataRoot
        var env = ProcessInfo.processInfo.environment
        env["NEXUS_PORT"] = "0"
        env["NEXUS_RESEARCH_DIR"] = dataRoot.appendingPathComponent("research").path
        env["NEXUS_READY_FILE"] = readyFile.path
        if env["NEXUS_OUTPUT_DIR"] == nil {
            let isolated = Bundle.main.object(forInfoDictionaryKey: "NexusQADataDirectory") != nil
                || env["NEXUS_NATIVE_DATA_DIR"] != nil
            env["NEXUS_OUTPUT_DIR"] = isolated
                ? dataRoot.appendingPathComponent("findings").path
                : files.urls(for: .documentDirectory, in: .userDomainMask)[0]
                    .appendingPathComponent("Nexus Research").path
        }
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        process.environment = env
        process.standardOutput = logHandle
        process.standardError = logHandle
        engine = process
        try process.run()
        timer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in self?.checkReady() }
    }

    private func checkReady() {
        polls += 1
        if let data = try? Data(contentsOf: readyFile),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Int],
           let port = json["port"], let base = URL(string: "http://127.0.0.1:\(port)") {
            timer?.invalidate(); timer = nil
            origin = base
            web.load(URLRequest(url: base))
            try? files.removeItem(at: readyFile.deletingLastPathComponent())
        } else if polls > 200 || engine?.isRunning == false {
            timer?.invalidate(); timer = nil
            showError("The local service did not start. See desktop.log in the Nexus Research data folder.")
        }
    }

    private func showError(_ message: String) {
        let label = NSTextField(wrappingLabelWithString: message)
        label.font = NSFont.monospacedSystemFont(ofSize: 13, weight: .regular)
        label.frame = NSRect(x: 40, y: 40, width: 650, height: 120)
        window.contentView = NSView(frame: window.contentView!.bounds)
        window.contentView?.addSubview(label)
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if let base = origin, url.scheme == base.scheme, url.host == base.host, url.port == base.port {
            decisionHandler(.allow)
        } else {
            if action.navigationType == .linkActivated, url.scheme == "https" { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let url = message.frameInfo.request.url, let base = origin,
              url.host == base.host, url.port == base.port, url.scheme == base.scheme else { return }
        if message.name == "chooseFolder" {
            let panel = NSOpenPanel()
            panel.canChooseDirectories = true
            panel.canChooseFiles = false
            panel.canCreateDirectories = true
            panel.allowsMultipleSelection = false
            panel.prompt = "Use folder"
            if let body = message.body as? [String: String], let path = body["path"], path.hasPrefix("/") {
                panel.directoryURL = URL(fileURLWithPath: path, isDirectory: true)
            }
            panel.beginSheetModal(for: window) { result in
                guard result == .OK, let path = panel.url?.path,
                      let encoded = try? JSONSerialization.data(withJSONObject: [path]),
                      let json = String(data: encoded, encoding: .utf8) else { return }
                self.web.evaluateJavaScript("window.dispatchEvent(new CustomEvent('nexus:directory', {detail: \(json)[0]}))")
            }
        } else if message.name == "reveal", let body = message.body as? [String: String],
                  let path = body["path"], path.hasPrefix("/") {
            NSWorkspace.shared.open(URL(fileURLWithPath: path, isDirectory: true))
        } else if message.name == "save", let body = message.body as? [String: String],
                  let name = body["name"], let text = body["text"], text.utf8.count <= 10_000_000 {
            let panel = NSSavePanel()
            panel.nameFieldStringValue = String(URL(fileURLWithPath: name).lastPathComponent.prefix(150))
            panel.beginSheetModal(for: window) { result in
                if result == .OK, let destination = panel.url {
                    do { try Data(text.utf8).write(to: destination, options: .atomic) }
                    catch { let alert = NSAlert(error: error); alert.beginSheetModal(for: self.window) }
                }
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        timer?.invalidate()
        guard let process = engine, process.isRunning else { return .terminateNow }
        process.terminate()
        DispatchQueue.global().async {
            for _ in 0..<40 {
                if !process.isRunning { break }
                Thread.sleep(forTimeInterval: 0.1)
            }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }
}

let app = NSApplication.shared
let delegate = Desktop()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
