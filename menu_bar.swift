import AppKit
import Darwin

enum SettingsError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let text): text
        }
    }
}

final class SettingsStore {
    let url: URL
    private(set) var values: [String: Any] = [:]

    init(url: URL) throws {
        self.url = url
        try reload()
    }

    func reload() throws {
        guard FileManager.default.fileExists(atPath: url.path) else {
            values = [:]
            return
        }
        let object = try JSONSerialization.jsonObject(with: Data(contentsOf: url))
        guard let dictionary = object as? [String: Any] else {
            throw SettingsError.message("The settings file must contain a JSON object.")
        }
        values = dictionary
    }

    func string(_ key: String, default fallback: String) -> String {
        values[key] as? String ?? fallback
    }

    func bool(_ key: String, default fallback: Bool) -> Bool {
        values[key] as? Bool ?? fallback
    }

    func set(_ value: Any, for key: String) throws {
        let directory = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let lockURL = url.deletingPathExtension().appendingPathExtension("lock")
        let lock = Darwin.open(lockURL.path, O_CREAT | O_RDWR, 0o600)
        guard lock >= 0 else { throw posixError("Could not open the settings lock") }
        defer { Darwin.close(lock) }
        guard flock(lock, LOCK_EX) == 0 else { throw posixError("Could not lock the settings file") }
        defer { flock(lock, LOCK_UN) }

        try reload()
        if key == "routing_preference" {
            values.removeValue(forKey: "economy")
        }
        values[key] = value
        var data = try JSONSerialization.data(withJSONObject: values, options: [.prettyPrinted, .sortedKeys])
        data.append(0x0A)
        let temporary = directory.appendingPathComponent(".\(url.lastPathComponent).\(UUID().uuidString)")
        let output = Darwin.open(temporary.path, O_CREAT | O_EXCL | O_WRONLY, 0o600)
        guard output >= 0 else { throw posixError("Could not create the temporary settings file") }
        var outputIsOpen = true
        defer { if outputIsOpen { Darwin.close(output) } }
        do {
            try data.withUnsafeBytes { bytes in
                var offset = 0
                while offset < bytes.count {
                    let count = Darwin.write(output, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
                    guard count > 0 else { throw posixError("Could not write settings") }
                    offset += count
                }
            }
            guard fsync(output) == 0 else { throw posixError("Could not save settings") }
            Darwin.close(output)
            outputIsOpen = false
            guard rename(temporary.path, url.path) == 0 else { throw posixError("Could not replace settings") }
        } catch {
            unlink(temporary.path)
            throw error
        }
    }

    private func posixError(_ message: String) -> SettingsError {
        SettingsError.message("\(message): \(String(cString: strerror(errno)))")
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var store: SettingsStore!
    private var models: [String] = []
    private var efforts: [String] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            store = try SettingsStore(url: Self.configURL())
        } catch {
            show(error)
            NSApp.terminate(nil)
            return
        }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(
            systemSymbolName: "slider.horizontal.3",
            accessibilityDescription: "Jev Codex settings"
        )
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
    }

    func menuWillOpen(_ menu: NSMenu) {
        do {
            try store.reload()
            try loadCatalog()
        } catch { show(error) }
        menu.removeAllItems()

        let title = NSMenuItem(title: "Jev Codex", action: nil, keyEquivalent: "")
        title.isEnabled = false
        menu.addItem(title)
        menu.addItem(.separator())
        menu.addItem(submenu(
            title: "Routing frequency",
            values: [("Once per thread", "thread"), ("Every turn", "turn")],
            selected: store.string("routing_mode", default: "thread"),
            action: #selector(selectRoutingMode(_:))
        ))
        let timing = NSMenuItem(title: "Route choices apply at the next selection", action: nil, keyEquivalent: "")
        timing.isEnabled = false
        menu.addItem(timing)
        menu.addItem(submenu(
            title: "Routing preference",
            values: [("Lowest usage", "lowest_usage"), ("Lower usage", "lower_usage"),
                     ("Balanced", "balanced"), ("Higher quality", "higher_quality"),
                     ("Highest quality", "highest_quality")],
            selected: routingPreference(),
            action: #selector(selectRoutingPreference(_:))
        ))
        menu.addItem(submenu(
            title: "Maximum effort",
            values: [("Automatic", "automatic"), ("High", "high"),
                     ("Extra high", "xhigh"), ("Maximum", "max")],
            selected: store.string("maximum_effort", default: "automatic"),
            action: #selector(selectMaximumEffort(_:))
        ))
        if !models.isEmpty {
            menu.addItem(checklist(
                title: "Model pool", values: models, selected: selected("allow_model", from: models),
                action: #selector(toggleModel(_:))))
        }
        if !efforts.isEmpty {
            let labels = ["low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high",
                          "max": "Maximum", "ultra": "Ultra"]
            menu.addItem(checklist(
                title: "Effort pool", values: efforts, selected: selected("allow_effort", from: efforts),
                action: #selector(toggleEffort(_:)), labels: labels))
        }
        let usageRouting = NSMenuItem(title: "Use remaining quota when routing", action: #selector(toggleUsageRouting(_:)), keyEquivalent: "")
        usageRouting.target = self
        usageRouting.state = store.string("usage_policy", default: "balanced") == "quality" ? .off : .on
        menu.addItem(usageRouting)
        let usage = NSMenuItem(title: "Show usage before each turn", action: #selector(toggleUsage(_:)), keyEquivalent: "")
        usage.target = self
        usage.state = store.bool("show_usage", default: true) ? .on : .off
        menu.addItem(usage)
        menu.addItem(.separator())
        let reveal = NSMenuItem(title: "Show settings file", action: #selector(showSettingsFile), keyEquivalent: "")
        reveal.target = self
        menu.addItem(reveal)
        let quit = NSMenuItem(title: "Quit Jev Codex settings", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(quit)
    }

    private func submenu(title: String, values: [(String, String)], selected: String, action: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        let menu = NSMenu(title: title)
        for (label, value) in values {
            let option = NSMenuItem(title: label, action: action, keyEquivalent: "")
            option.target = self
            option.representedObject = value
            option.state = value == selected ? .on : .off
            menu.addItem(option)
        }
        item.submenu = menu
        return item
    }

    private func checklist(title: String, values: [String], selected: Set<String>, action: Selector,
                           labels: [String: String] = [:]) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        let menu = NSMenu(title: title)
        let buttons = values.map { value in
            let button = NSButton(checkboxWithTitle: labels[value] ?? value, target: self, action: action)
            button.identifier = NSUserInterfaceItemIdentifier(value)
            button.state = selected.contains(value) ? .on : .off
            return button
        }
        let width = ceil((buttons.map(\.fittingSize.width).max() ?? 0) + 24)
        for (index, button) in buttons.enumerated() {
            let top = index == 0 ? 6.0 : 0.0
            let bottom = index == buttons.count - 1 ? 6.0 : 0.0
            let view = NSView(frame: NSRect(x: 0, y: 0, width: width, height: 24 + top + bottom))
            button.frame = NSRect(x: 12, y: bottom + (24 - button.fittingSize.height) / 2,
                                  width: button.fittingSize.width, height: button.fittingSize.height)
            view.addSubview(button)
            let option = NSMenuItem()
            option.view = view
            menu.addItem(option)
        }
        item.submenu = menu
        return item
    }

    private func loadCatalog() throws {
        let url = store.url.deletingLastPathComponent().appendingPathComponent("catalog.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        guard let catalog = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any],
              let models = catalog["models"] as? [String], let efforts = catalog["efforts"] as? [String] else {
            throw SettingsError.message("The model catalog is invalid.")
        }
        self.models = models
        self.efforts = efforts
    }

    private func selected(_ key: String, from all: [String]) -> Set<String> {
        Set(store.values[key] as? [String] ?? all)
    }

    private func toggle(_ sender: NSButton, key: String, all: [String]) {
        guard let value = sender.identifier?.rawValue else { return }
        var values = selected(key, from: all)
        if values.contains(value) { values.remove(value) } else { values.insert(value) }
        guard !values.isEmpty else {
            sender.state = .on
            show(SettingsError.message("Keep at least one option in the \(key == "allow_model" ? "model" : "effort") pool."))
            return
        }
        do {
            try store.set(values == Set(all) ? NSNull() : Array(values).sorted(), for: key)
        } catch {
            sender.state = sender.state == .on ? .off : .on
            show(error)
        }
    }

    private func routingPreference() -> String {
        if let value = store.values["routing_preference"] as? String {
            return value
        }
        return ["xhigh": "lowest_usage", "high": "lower_usage", "medium": "balanced",
                "low": "higher_quality"][store.string("economy", default: "medium")] ?? "balanced"
    }

    @objc private func selectRoutingPreference(_ sender: NSMenuItem) {
        save(sender.representedObject as! String, key: "routing_preference")
    }

    @objc private func selectRoutingMode(_ sender: NSMenuItem) {
        save(sender.representedObject as! String, key: "routing_mode")
    }

    @objc private func selectMaximumEffort(_ sender: NSMenuItem) {
        save(sender.representedObject as! String, key: "maximum_effort")
    }

    @objc private func toggleModel(_ sender: NSButton) {
        toggle(sender, key: "allow_model", all: models)
    }

    @objc private func toggleEffort(_ sender: NSButton) {
        toggle(sender, key: "allow_effort", all: efforts)
    }

    @objc private func toggleUsage(_ sender: NSMenuItem) {
        save(sender.state != .on, key: "show_usage")
    }

    @objc private func toggleUsageRouting(_ sender: NSMenuItem) {
        save(sender.state == .on ? "quality" : "balanced", key: "usage_policy")
    }

    @objc private func showSettingsFile() {
        NSWorkspace.shared.activateFileViewerSelecting([store.url])
    }

    private func save(_ value: Any, key: String) {
        do { try store.set(value, for: key) } catch { show(error) }
    }

    private func show(_ error: Error) {
        let alert = NSAlert(error: error)
        alert.messageText = "Jev Codex could not update settings"
        alert.runModal()
    }

    static func configURL() -> URL {
        let arguments = CommandLine.arguments
        if let index = arguments.firstIndex(of: "--config"), arguments.indices.contains(index + 1) {
            return URL(fileURLWithPath: arguments[index + 1])
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/jev-codex/config.json")
    }
}

@main
enum JevCodexSettings {
    static func main() {
        if CommandLine.arguments.contains("--check") {
            do {
                let store = try SettingsStore(url: AppDelegate.configURL())
                try store.set("turn", for: "routing_mode")
                try store.set("highest_quality", for: "routing_preference")
                try store.set("xhigh", for: "maximum_effort")
                try store.set("balanced", for: "usage_policy")
                try store.set(["gpt-6-astra"], for: "allow_model")
                try store.set(["low", "high"], for: "allow_effort")
                try store.reload()
                guard store.string("routing_mode", default: "") == "turn" else {
                    throw SettingsError.message("The saved routing frequency did not reload.")
                }
                guard store.string("routing_preference", default: "") == "highest_quality" else {
                    throw SettingsError.message("The saved routing preference did not reload.")
                }
                guard store.string("maximum_effort", default: "") == "xhigh" else {
                    throw SettingsError.message("The saved maximum effort did not reload.")
                }
                guard store.string("usage_policy", default: "") == "balanced" else {
                    throw SettingsError.message("The saved usage routing setting did not reload.")
                }
                guard store.values["allow_model"] as? [String] == ["gpt-6-astra"],
                      store.values["allow_effort"] as? [String] == ["low", "high"] else {
                    throw SettingsError.message("The saved route pools did not reload.")
                }
                print("Settings check passed.")
            } catch {
                fputs("Settings check failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
            return
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
