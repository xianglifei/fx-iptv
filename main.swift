// 联通电视 — WKWebView 独立窗口壳
// 职责:拉起 Python 服务 + 开一个原生窗口加载频道墙;退出时带走服务。
import AppKit
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var webview: WKWebView!
    var server: Process?

    func applicationDidFinishLaunching(_ note: Notification) {
        let res = Bundle.main.resourceURL!
        // python 路径:优先用打包时记录的,再按常见安装位置兜底(换机可直接用)
        let fromBundle = ((try? String(contentsOfFile: res.appendingPathComponent("python_path.txt").path,
                                            encoding: .utf8)) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let pythonPath = [fromBundle,
                          "/opt/homebrew/bin/python3",   // Homebrew (Apple Silicon)
                          "/usr/local/bin/python3",      // Homebrew (Intel)
                          "/usr/bin/python3"             // 系统自带(Command Line Tools)
        ].first { !$0.isEmpty && FileManager.default.isExecutableFile(atPath: $0) }
            ?? "/usr/bin/python3"

        let p = Process()
        p.executableURL = URL(fileURLWithPath: pythonPath)
        p.arguments = [res.appendingPathComponent("server.py").path,
                       "--root", res.path, "--no-browser"]
        // 服务日志落盘(放在分片目录之外,避免被换台清理误删)
        let logPath = "/tmp/mac-iptv-server.log"
        if !FileManager.default.fileExists(atPath: logPath) {
            FileManager.default.createFile(atPath: logPath, contents: nil)
        }
        if let fh = FileHandle(forWritingAtPath: logPath) {
            fh.seekToEndOfFile()
            p.standardOutput = fh
            p.standardError = fh
        }
        // 服务进程意外退出 => 应用跟着退出,避免白窗口
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                if self?.window?.isVisible == true { NSApp.terminate(nil) }
            }
        }
        try? p.run()
        server = p

        webview = WKWebView(frame: NSRect(x: 0, y: 0, width: 1280, height: 780),
                            configuration: WKWebViewConfiguration())
        window = NSWindow(contentRect: webview.frame,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "联通电视"
        window.contentView = webview
        window.center()
        window.setFrameAutosaveName("UnicomTVMain")
        window.makeKeyAndOrderFront(nil)

        let menu = NSMenu()
        let item = NSMenuItem()
        let sub = NSMenu()
        sub.addItem(NSMenuItem(title: "退出联通电视",
                               action: #selector(NSApplication.terminate(_:)),
                               keyEquivalent: "q"))
        item.submenu = sub
        menu.addItem(item)
        NSApp.mainMenu = menu

        waitServerThenLoad()
        NSApp.activate(ignoringOtherApps: true)
    }

    private func waitServerThenLoad(retry: Int = 60) {
        var req = URLRequest(url: URL(string: "http://127.0.0.1:8787/api/status")!)
        req.timeoutInterval = 1
        URLSession.shared.dataTask(with: req) { [weak self] _, resp, _ in
            let ok = (resp as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async {
                if ok {
                    self?.webview.load(URLRequest(url: URL(string: "http://127.0.0.1:8787")!))
                } else if retry > 0 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                        self?.waitServerThenLoad(retry: retry - 1)
                    }
                } else {
                    // 15 秒仍无服务:多半是这台机器缺 Python 3 或 ffmpeg
                    let a = NSAlert()
                    a.messageText = "本地服务启动失败"
                    a.informativeText = "未检测到可用的 Python 3 或 ffmpeg。\n请安装后重试:\n  brew install python3 ffmpeg\n(日志: /tmp/mac-iptv-server.log)"
                    a.addButton(withTitle: "退出")
                    a.runModal()
                    NSApp.terminate(nil)
                }
            }
        }.resume()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ note: Notification) {
        server?.terminate()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
