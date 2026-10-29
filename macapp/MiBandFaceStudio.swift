import SwiftUI
import AppKit
import Combine
import UniformTypeIdentifiers
import ImageIO

@main
struct MiBandFaceStudioApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .frame(minWidth: 1000, minHeight: 720)
                .preferredColorScheme(.dark)
        }
        .windowStyle(.titleBar)
        .defaultSize(width: 1080, height: 800)
    }
}

struct BuildResult {
    let ok: Bool
    let output: String
}

enum ElementKind {
    case time, steps, battery
}

struct ContentView: View {
    @State private var backgroundURL: URL?
    @State private var previewURL: URL?
    @State private var bgImage: NSImage?
    @State private var title = "DataDash"
    @State private var status = "就绪"
    @State private var log = ""
    @State private var isBuilding = false
    @State private var lastOutput: URL?
    @State private var resultImageURL: URL?

    @State private var timePos = CGPoint(x: 14, y: 6)
    @State private var stepsPos = CGPoint(x: 29, y: 372)
    @State private var battPos = CGPoint(x: 47, y: 18)
    @State private var timeSize: CGFloat = 94
    @State private var stepsSize: CGFloat = 40
    @State private var battSize: CGFloat = 11
    @State private var selected: ElementKind?
    @State private var dragStart: CGPoint?
    @State private var now = Date()

    private let designW: CGFloat = 336
    private let designH: CGFloat = 480
    private let accent = Color(red: 0.31, green: 0.89, blue: 0.76)
    private let muted = Color(red: 0.62, green: 0.70, blue: 0.71)
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()
    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "M月d日"
        return f
    }()

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f
    }()

    private var sampleTime: String { Self.timeFormatter.string(from: now) }
    private var currentDate: String { Self.dateFormatter.string(from: now) }

    private var nativeDir: URL {
        Bundle.main.resourceURL!.appendingPathComponent("native", isDirectory: true)
    }
    private var defaultBG: URL {
        nativeDir.appendingPathComponent("assets/background.png")
    }
    private var defaultPV: URL {
        nativeDir.appendingPathComponent("assets/preview.png")
    }
    private var outputDir: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MiBandFace", isDirectory: true)
    }

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [Color(red: 0.05, green: 0.10, blue: 0.11),
                         Color(red: 0.02, green: 0.04, blue: 0.05)],
                startPoint: .topLeading, endPoint: .bottomTrailing
            ).ignoresSafeArea()

            HStack(alignment: .top, spacing: 26) {
                canvasPane
                controlsPane
            }
            .padding(24)
        }
        .onAppear {
            if bgImage == nil {
                bgImage = NSImage(contentsOf: defaultBG)
            }
        }
        .onReceive(timer) { now = $0 }
    }

    // MARK: - canvas

    private var canvasPane: some View {
        VStack(spacing: 12) {
            GeometryReader { geo in
                let scale = min(geo.size.width / designW, geo.size.height / designH)
                let cw = designW * scale
                let ch = designH * scale
                ZStack(alignment: .topLeading) {
                    if let img = bgImage {
                        Image(nsImage: img)
                            .resizable()
                            .interpolation(.high)
                            .frame(width: cw, height: ch)
                    } else {
                        Rectangle().fill(Color.black).frame(width: cw, height: ch)
                    }

                    dateOverlay(scale: scale)
                    weekdayOverlay(scale: scale)
                    tempOverlay(scale: scale)
                    timeOverlay(scale: scale)
                    stepsOverlay(scale: scale)
                    batteryOverlay(scale: scale)
                }
                .frame(width: cw, height: ch)
                .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .stroke(accent.opacity(0.25), lineWidth: 1)
                )
                .shadow(color: .black.opacity(0.45), radius: 24, y: 12)
                .position(x: geo.size.width / 2, y: geo.size.height / 2)
            }
            .frame(width: 390, height: 558)

            HStack(spacing: 6) {
                Image(systemName: "hand.draw")
                    .font(.system(size: 11))
                Text("拖动时间 / 步数 / 电量调整位置")
                    .font(.system(size: 11, weight: .medium))
            }
            .foregroundStyle(.secondary)
        }
    }

    private func dateOverlay(scale: CGFloat) -> some View {
        Text(currentDate)
            .font(.custom("Avenir Next Condensed", size: 14 * scale))
            .fontWeight(.semibold)
            .foregroundStyle(.white)
            .offset(x: 16 * scale, y: 134 * scale)
            .allowsHitTesting(false)
    }

    private func weekdayOverlay(scale: CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            Text("WED")
                .font(.custom("Avenir Next Condensed", size: 9 * scale))
                .foregroundStyle(muted)
                .offset(x: 300 * scale, y: 52 * scale)
            Text("周三")
                .font(.custom("Hiragino Sans GB", size: 21 * scale))
                .foregroundStyle(.white)
                .offset(x: 276 * scale, y: 63 * scale)
        }
        .allowsHitTesting(false)
    }

    private func tempOverlay(scale: CGFloat) -> some View {
        HStack(alignment: .top, spacing: 1 * scale) {
            Text("21")
                .font(.custom("Avenir Next Condensed", size: 47 * scale))
                .fontWeight(.semibold)
                .foregroundStyle(.white)
            Text("°")
                .font(.custom("Avenir Next Condensed", size: 26 * scale))
                .foregroundStyle(Color(red: 0.435, green: 0.847, blue: 0.765))
                .offset(y: 2 * scale)
        }
        .offset(x: 84 * scale, y: 170 * scale)
        .allowsHitTesting(false)
    }

    private func timeOverlay(scale: CGFloat) -> some View {
        Text(sampleTime)
            .font(.custom("Avenir Next Condensed", size: timeSize * scale))
            .fontWeight(.bold)
            .foregroundStyle(.white)
            .fixedSize()
            .padding(4)
            .background(selected == .time ? accent.opacity(0.10) : .clear)
            .overlay {
                if selected == .time {
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(accent, style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
                }
            }
            .offset(x: timePos.x * scale - 4, y: timePos.y * scale - 4)
            .gesture(dragGesture(for: .time, binding: $timePos, scale: scale))
            .onTapGesture { selected = .time }
    }

    private func stepsOverlay(scale: CGFloat) -> some View {
        HStack(alignment: .lastTextBaseline, spacing: 3 * scale) {
            Text("8432")
                .font(.custom("Avenir Next Condensed", size: stepsSize * scale))
                .fontWeight(.semibold)
                .foregroundStyle(.white)
            Text("步")
                .font(.custom("Hiragino Sans GB", size: 9 * scale))
                .foregroundStyle(muted)
        }
        .fixedSize()
        .padding(4)
        .background(selected == .steps ? accent.opacity(0.10) : .clear)
        .overlay {
            if selected == .steps {
                RoundedRectangle(cornerRadius: 6)
                    .stroke(accent, style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
            }
        }
        .offset(x: stepsPos.x * scale - 4, y: stepsPos.y * scale - 4)
        .gesture(dragGesture(for: .steps, binding: $stepsPos, scale: scale))
        .onTapGesture { selected = .steps }
    }

    private func batteryOverlay(scale: CGFloat) -> some View {
        HStack(alignment: .lastTextBaseline, spacing: 1 * scale) {
            Text("76")
                .font(.custom("Avenir Next Condensed", size: battSize * scale))
                .fontWeight(.semibold)
                .foregroundStyle(muted)
            Text("%")
                .font(.custom("Avenir Next Condensed", size: battSize * scale))
                .foregroundStyle(muted)
        }
        .fixedSize()
        .padding(4)
        .background(selected == .battery ? accent.opacity(0.10) : .clear)
        .overlay {
            if selected == .battery {
                RoundedRectangle(cornerRadius: 6)
                    .stroke(accent, style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
            }
        }
        .offset(x: battPos.x * scale - 4, y: battPos.y * scale - 4)
        .gesture(dragGesture(for: .battery, binding: $battPos, scale: scale))
        .onTapGesture { selected = .battery }
    }

    private func dragGesture(for kind: ElementKind, binding: Binding<CGPoint>,
                             scale: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                if dragStart == nil {
                    dragStart = binding.wrappedValue
                    selected = kind
                }
                guard let start = dragStart else { return }
                let nx = start.x + value.translation.width / scale
                let ny = start.y + value.translation.height / scale
                binding.wrappedValue = CGPoint(
                    x: min(max(0, nx), designW - 40),
                    y: min(max(0, ny), designH - 40)
                )
            }
            .onEnded { _ in dragStart = nil }
    }

    // MARK: - controls

    private var controlsPane: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 5) {
                Text("腕上数据仪表")
                    .font(.system(size: 27, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
                Text("小米手环 10 Pro · 原生 .face 编辑器")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }

            VStack(alignment: .leading, spacing: 10) {
                fileRow(label: "底图", url: backgroundURL, fallback: "background.png") {
                    pickImage { url in
                        guard let size = pixelSize(url), size.0 == 336, size.1 == 480 else {
                            status = "底图必须是 336×480"
                            return
                        }
                        backgroundURL = url
                        bgImage = NSImage(contentsOf: url)
                        status = "底图已更新"
                    }
                }
                fileRow(label: "预览图", url: previewURL, fallback: "preview.png") {
                    pickImage { url in
                        guard let size = pixelSize(url), size.0 == 336, size.1 == 480 else {
                            status = "预览图必须是 336×480"
                            return
                        }
                        previewURL = url
                        status = "预览图已更新"
                    }
                }
                HStack(spacing: 10) {
                    Text("名称")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(.secondary)
                        .frame(width: 40, alignment: .leading)
                    TextField("表盘名称", text: $title)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12, design: .rounded))
                }
            }
            .padding(13)
            .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13).stroke(Color.white.opacity(0.07)))

            VStack(alignment: .leading, spacing: 8) {
                sizeSlider(label: "时间", value: $timeSize, range: 60...110)
                sizeSlider(label: "步数", value: $stepsSize, range: 28...48)
                sizeSlider(label: "电量", value: $battSize, range: 9...14)
            }
            .padding(13)
            .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13).stroke(Color.white.opacity(0.07)))

            HStack(spacing: 10) {
                Button {
                    buildFace()
                } label: {
                    HStack(spacing: 7) {
                        if isBuilding { ProgressView().controlSize(.small) }
                        Text(isBuilding ? "生成中…" : "生成 .face")
                            .font(.system(size: 13, weight: .semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 9)
                }
                .buttonStyle(.borderedProminent)
                .tint(accent)
                .foregroundStyle(Color(red: 0.02, green: 0.09, blue: 0.08))
                .disabled(isBuilding)

                Button("打开输出") {
                    try? FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)
                    NSWorkspace.shared.open(outputDir)
                }
                .buttonStyle(.bordered)
                .padding(.vertical, 9)
            }

            HStack(spacing: 10) {
                Button("重置布局") {
                    resetLayout()
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button("查看结果图") {
                    if let u = resultImageURL {
                        NSWorkspace.shared.open(u)
                    }
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(resultImageURL == nil)

                Spacer()

                Text("坐标 \(Int(timePos.x)),\(Int(timePos.y)) · \(Int(stepsPos.x)),\(Int(stepsPos.y)) · \(Int(battPos.x)),\(Int(battPos.y))")
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(.tertiary)
            }

            HStack(spacing: 8) {
                Circle()
                    .fill(status.hasPrefix("失败") ? Color.red : accent)
                    .frame(width: 7, height: 7)
                Text(status)
                    .font(.system(size: 11, design: .rounded))
                    .foregroundStyle(.secondary)
                Spacer()
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("日志")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.secondary)
                ScrollView {
                    Text(log.isEmpty ? "拖动左侧元素，点「生成 .face」。" : log)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(log.isEmpty ? .tertiary : .secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(height: 108)
                .padding(9)
                .background(Color.black.opacity(0.35), in: RoundedRectangle(cornerRadius: 9))
            }

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func sizeSlider(label: String, value: Binding<CGFloat>,
                            range: ClosedRange<CGFloat>) -> some View {
        HStack(spacing: 10) {
            Text(label)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 32, alignment: .leading)
            Slider(value: value, in: range, step: 1)
                .tint(accent)
            Text("\(Int(value.wrappedValue))")
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(.secondary)
                .frame(width: 26, alignment: .trailing)
        }
    }

    private func fileRow(label: String, url: URL?, fallback: String,
                         action: @escaping () -> Void) -> some View {
        HStack(spacing: 9) {
            Text(label)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 40, alignment: .leading)
            Text(url?.lastPathComponent ?? fallback)
                .font(.system(size: 11, design: .monospaced))
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer()
            Button("选择…", action: action)
                .buttonStyle(.bordered)
                .controlSize(.mini)
        }
    }

    private func pickImage(_ onPick: @escaping (URL) -> Void) {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.png, .jpeg]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if panel.runModal() == .OK, let url = panel.url {
            onPick(url)
        }
    }

    private func pixelSize(_ url: URL) -> (Int, Int)? {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any],
              let w = props[kCGImagePropertyPixelWidth] as? Int,
              let h = props[kCGImagePropertyPixelHeight] as? Int else { return nil }
        return (w, h)
    }

    private func resetLayout() {
        timePos = CGPoint(x: 14, y: 6)
        stepsPos = CGPoint(x: 29, y: 372)
        battPos = CGPoint(x: 47, y: 18)
        timeSize = 94
        stepsSize = 40
        battSize = 11
        selected = nil
    }

    private func buildFace() {
        guard !isBuilding else { return }
        isBuilding = true
        status = "正在生成…"
        log = ""
        let bg = backgroundURL ?? defaultBG
        let pv = previewURL ?? defaultPV
        let safeTitle = title.isEmpty ? "DataDash" : title
        do {
            try FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)
        } catch {
            status = "失败：无法创建输出目录"
            log = error.localizedDescription
            isBuilding = false
            return
        }
        let face = outputDir.appendingPathComponent("\(safeTitle).face")
        let mock = outputDir.appendingPathComponent("\(safeTitle)-preview.png")
        let layoutURL = outputDir.appendingPathComponent("\(safeTitle)-layout.json")

        let layout: [String: Any] = [
            "title": safeTitle,
            "time": ["x": Double(timePos.x), "y": Double(timePos.y), "size": Double(timeSize)],
            "steps": ["x": Double(stepsPos.x), "y": Double(stepsPos.y),
                      "size": Double(stepsSize), "digits": 5],
            "battery": ["x": Double(battPos.x), "y": Double(battPos.y),
                        "size": Double(battSize), "digits": 3]
        ]
        do {
            let data = try JSONSerialization.data(withJSONObject: layout, options: [.prettyPrinted])
            try data.write(to: layoutURL)
        } catch {
            status = "失败：布局写入失败"
            log = error.localizedDescription
            isBuilding = false
            return
        }

        DispatchQueue.global(qos: .userInitiated).async {
            let result = runPython(bg: bg, pv: pv, face: face, mock: mock,
                                   title: safeTitle, layout: layoutURL)
            DispatchQueue.main.async {
                isBuilding = false
                log = result.output
                if result.ok {
                    status = "已生成 \(face.lastPathComponent)"
                    lastOutput = face
                    resultImageURL = mock
                } else {
                    status = "失败：检查日志（需要 Pillow）"
                }
            }
        }
    }

    private func runPython(bg: URL, pv: URL, face: URL, mock: URL,
                           title: String, layout: URL) -> BuildResult {
        let script = nativeDir.appendingPathComponent("xwf_face.py")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = [
            script.path,
            "--background", bg.path,
            "--preview", pv.path,
            "--out", face.path,
            "--mock", mock.path,
            "--title", title,
            "--layout", layout.path
        ]
        p.currentDirectoryURL = nativeDir
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = nativeDir.path
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do {
            try p.run()
        } catch {
            return BuildResult(ok: false, output: "无法启动 Python：\(error.localizedDescription)")
        }
        p.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let out = String(data: data, encoding: .utf8) ?? ""
        return BuildResult(ok: p.terminationStatus == 0, output: out)
    }
}
