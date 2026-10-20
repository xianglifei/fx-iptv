#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联通电视 — Mac 本地 IPTV 机顶盒服务
rtp 组播 -> ffmpeg(-c copy) -> HLS -> 本地 HTTP
仅依赖 Python 标准库与 ffmpeg。
"""
import argparse
import atexit
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DIR = "/tmp/mac-iptv-live"
PORT = 8787


def find_ffmpeg():
    """GUI App 环境没有 Homebrew PATH,需要兜底绝对路径"""
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.exists(c):
            return c
    return "ffmpeg"


FFMPEG = find_ffmpeg()


def find_bin(name):
    p = shutil.which(name)
    if p:
        return p
    for c in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}", f"/usr/bin/{name}"):
        if os.path.exists(c):
            return c
    return name


FFPROBE = find_bin("ffprobe")

# ---------- 隔行扫描探测(对分片文件探测,按频道缓存) ----------

FIELD_CACHE = {}      # num -> 是否隔行
HISTORY = []          # 最近观看的频道号,新的在前
CACHE_PATH = os.path.expanduser("~/.mac-iptv/field_cache.json")
PROBE_DIR = "/tmp/mac-iptv-probe"


def load_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        FIELD_CACHE.update({int(k): bool(v) for k, v in data.get("field", {}).items()})
        HISTORY.extend(int(n) for n in data.get("history", []))
    except (OSError, ValueError, TypeError):
        pass


def save_cache():
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"field": {str(k): v for k, v in FIELD_CACHE.items()},
                       "history": HISTORY[:50]}, f)
        os.replace(tmp, CACHE_PATH)
    except OSError:
        pass


def record_history(num):
    if num in HISTORY:
        HISTORY.remove(num)
    HISTORY.insert(0, num)
    del HISTORY[50:]
    save_cache()


def probe_segment_interlaced(path):
    """返回 True/False;None 表示探测不出(不缓存,维持现状)"""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=field_order", "-of", "json", path],
            capture_output=True, text=True, timeout=5).stdout
        streams = json.loads(out).get("streams", [])
        if not streams:
            return None
        fo = streams[0].get("field_order")
        if fo in (None, ""):
            return None
        return fo not in ("progressive", "unknown")
    except Exception:
        return None

# ---------- 频道表解析 ----------

CAT_ORDER = ["央视", "卫视", "北京IPTV", "北京", "4K", "少儿"]


def extract(attr, key):
    m = re.search(key + r'="([^"]*)"', attr)
    return m.group(1) if m else ""


def categories(group_field):
    """'[高清]央视;[高清]北京IPTV;4K' -> ['央视','北京IPTV','4K']"""
    cats = []
    for part in group_field.split(";"):
        cat = re.sub(r"\[(高清|标清|超清|蓝光)\]", "", part).strip()
        if cat and cat not in cats:
            cats.append(cat)
    return cats


def parse_m3u(path):
    channels = []
    attr = name = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#EXTINF"):
                attr = line.partition(":")[2]
                name = attr.partition(",")[2].strip()
            elif line and not line.startswith("#") and name is not None:
                m = re.match(r"rtp://([\d.]+):(\d+)", line)
                if m:
                    group_field = extract(attr, "group-title")
                    channels.append({
                        "num": len(channels) + 1,
                        "name": re.sub(r"\[\d+\]", "", name).strip(),
                        "url": line,
                        "logo": extract(attr, "tvg-logo"),
                        "cats": categories(group_field),
                        "quality": "标清" if "标清" in group_field else
                                   ("4K" if "4K" in group_field else "高清"),
                    })
                attr = name = None
    return channels


CHANNELS = []


def build_tab_list():
    seen = []
    for ch in CHANNELS:
        for c in ch["cats"]:
            if c not in seen:
                seen.append(c)
    ordered = [c for c in CAT_ORDER if c in seen]
    return ordered + [c for c in seen if c not in ordered]


# ---------- 调谐器:同一时刻只跑一个 ffmpeg ----------

class Tuner:
    def __init__(self):
        self.proc = None
        self.channel = None
        self.started_at = 0.0
        self.checking = None          # 正在做隔行探测的频道号(未定案)
        self.lock = threading.Lock()

    def is_checking(self, num):
        with self.lock:
            return self.checking == num

    def _clear_dir(self):
        os.makedirs(LIVE_DIR, exist_ok=True)
        for f in os.listdir(LIVE_DIR):
            if f.endswith(".log"):
                continue
            try:
                os.remove(os.path.join(LIVE_DIR, f))
            except OSError:
                pass

    def _start(self, ch, interlaced):
        log = open(os.path.join(LIVE_DIR, "ffmpeg.log"), "wb")
        # 隔行信号(1080i):yadif 反交错 + VideoToolbox 硬件编码 25p;
        # 逐行信号:视频直接拷贝,零转码
        if interlaced:
            vopts = ["-vf", "yadif=0:-1:0",
                     "-c:v", "h264_videotoolbox", "-allow_sw", "1",
                     "-b:v", "8M", "-maxrate", "10M", "-bufsize", "14M",
                     "-r", "25", "-pix_fmt", "yuv420p",
                     "-force_key_frames", "expr:gte(t,n_forced*1)"]
        else:
            vopts = ["-c:v", "copy"]
        cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "warning",
            "-fflags", "nobuffer",
            # 限制输入探测量,显著加快起播(默认探测会多吃几秒)
            "-analyzeduration", "2M", "-probesize", "2M",
            "-i", ch["url"],
            *vopts,
            # 音频必须转 AAC——联通源是 MP2/AC-3,浏览器 MSE 不支持
            "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000",
            "-avoid_negative_ts", "make_zero",
            "-f", "hls", "-hls_time", "1", "-hls_list_size", "6",
            "-hls_flags", "delete_segments+independent_segments",
            "-hls_segment_filename", os.path.join(LIVE_DIR, "seg_%05d.ts"),
            os.path.join(LIVE_DIR, "index.m3u8"),
        ]
        self.proc = subprocess.Popen(cmd, stdout=log, stderr=log)
        self.channel = ch
        self.started_at = time.time()

    def zap(self, ch):
        with self.lock:
            self._stop_locked()
            self._clear_dir()
            known = FIELD_CACHE.get(ch["num"])
            self.checking = ch["num"] if known is None else None
            self._start(ch, bool(known))
        record_history(ch["num"])
        if known is None:
            # 首次播放该频道:等第一个分片落地后探测是否隔行,
            # 是则重启为反交错管线(定案前不向客户端发放播放列表)
            try:
                self._check_first_segment(ch)
            finally:
                with self.lock:
                    if self.checking == ch["num"]:
                        self.checking = None

    def _check_first_segment(self, ch):
        import glob
        deadline = time.time() + 10
        while time.time() < deadline:
            with self.lock:
                if self.channel is None or self.channel["num"] != ch["num"]:
                    return          # 用户已换台,放弃
            segs = sorted(glob.glob(os.path.join(LIVE_DIR, "seg_*.ts")))
            if segs:
                inter = probe_segment_interlaced(segs[0])
                if inter is not None:
                    FIELD_CACHE[ch["num"]] = inter
                if inter:
                    with self.lock:
                        if self.channel and self.channel["num"] == ch["num"]:
                            self._stop_locked()
                            self._clear_dir()
                            self._start(ch, True)
                return
            time.sleep(0.2)

    def _stop_locked(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None
        self.channel = None

    def stop(self):
        with self.lock:
            self._stop_locked()

    def status(self):
        with self.lock:
            if self.channel is None:
                return {"playing": False}
            alive = self.proc is not None and self.proc.poll() is None
            playlist = os.path.join(LIVE_DIR, "index.m3u8")
            try:
                seg_age = time.time() - max(
                    os.path.getmtime(os.path.join(LIVE_DIR, f))
                    for f in os.listdir(LIVE_DIR) if f.endswith(".ts"))
            except ValueError:
                seg_age = None
            fresh = seg_age is not None and seg_age < 10
            return {
                "playing": True,
                "channel": self.channel["num"],
                "name": self.channel["name"],
                "signal": bool(alive and (fresh or time.time() - self.started_at < 5)),
                "segment_age": seg_age,
            }


TUNER = Tuner()


# ---------- 后台频道预探测 ----------
# 对"最近观看 + 常用前12"频道,各拷贝 1 秒落盘后做文件探测(可靠),
# 结果写入 FIELD_CACHE 并持久化;之后首次换台即直达正确管线。

def _network_probe_channel(num, url):
    os.makedirs(PROBE_DIR, exist_ok=True)
    out = os.path.join(PROBE_DIR, f"probe_{num}.ts")
    try:
        # 必须截满 2.5 秒:GOP 为 1 秒,短了片段里可能没有 SPS/PPS,探测不出
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error",
             "-fflags", "nobuffer", "-analyzeduration", "2M", "-probesize", "2M",
             "-i", url, "-t", "2.5", "-c", "copy", "-f", "mpegts", out],
            timeout=10, capture_output=True)
        return probe_segment_interlaced(out)
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def _preprobe_worker(work):
    while True:
        num = work.get()
        try:
            if num is None or not 1 <= num <= len(CHANNELS):
                continue
            if num in FIELD_CACHE:
                continue
            ch = CHANNELS[num - 1]
            result = _network_probe_channel(ch["num"], ch["url"])
            if result is not None:
                FIELD_CACHE[ch["num"]] = result
                save_cache()
        finally:
            work.task_done()


def start_preprobe():
    nums = []
    for n in HISTORY:
        if n not in nums:
            nums.append(n)
    for ch in CHANNELS[:12]:
        if ch["num"] not in nums:
            nums.append(ch["num"])
    todo = [n for n in nums if n not in FIELD_CACHE]
    if not todo:
        return
    work = queue.Queue()
    for n in todo:
        work.put(n)
    for _ in range(4):
        threading.Thread(target=_preprobe_worker, args=(work,),
                         daemon=True).start()
    print(f"后台预探测 {len(todo)} 个频道(历史+常用)…", flush=True)


# ---------- HTTP 服务 ----------

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "/live/" not in (args[0] if args else ""):
            sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, body, ctype, cache="no-store"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, cache="no-store"):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except (FileNotFoundError, IsADirectoryError):
            self._send(404, "not found", "text/plain")
            return
        ext = os.path.splitext(path)[1]
        self._send(200, data, CONTENT_TYPES.get(ext, "application/octet-stream"), cache)

    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._file(os.path.join(BASE_DIR, "static", "index.html"), cache="no-cache")
        elif path.startswith("/static/"):
            self._file(os.path.normpath(os.path.join(BASE_DIR, path.lstrip("/"))),
                       cache="no-cache")
        elif path == "/api/channels":
            chans = [dict(ch, cached=ch["num"] in FIELD_CACHE) for ch in CHANNELS]
            self._send(200, json.dumps({"channels": chans, "tabs": build_tab_list()},
                                       ensure_ascii=False), "application/json")
        elif path == "/api/status":
            self._send(200, json.dumps(TUNER.status(), ensure_ascii=False),
                       "application/json")
        elif path == "/api/stop":
            TUNER.stop()
            self._send(200, '{"stopped":true}', "application/json")
        elif path == "/live/index.m3u8":
            self._tune(qs.get("ch", [""])[0])
        elif path.startswith("/live/"):
            name = os.path.basename(path)
            self._file(os.path.join(LIVE_DIR, name))
        else:
            self._send(404, "not found", "text/plain")

    def _tune(self, ch_str):
        playlist = os.path.join(LIVE_DIR, "index.m3u8")
        if not ch_str:
            # 无参数:返回当前正在播放频道的列表
            st = TUNER.status()
            if st.get("playing") and os.path.exists(playlist):
                self._file(playlist)
            else:
                self._send(503, '{"error":"not playing"}', "application/json")
            return
        try:
            num = int(ch_str)
        except ValueError:
            self._send(400, '{"error":"bad channel"}', "application/json")
            return
        if not 1 <= num <= len(CHANNELS):
            self._send(404, '{"error":"no such channel"}', "application/json")
            return
        ch = CHANNELS[num - 1]
        # 已在该频道且进程存活、且隔行探测已定案,才直接返回当前列表
        st = TUNER.status()
        if not (st.get("playing") and st.get("channel") == num and st["signal"]) \
                and not TUNER.is_checking(num):
            TUNER.zap(ch)
        # 等播放列表生成(首播隔行频道需两段启动,放宽到 15 秒)
        deadline = time.time() + 15
        while time.time() < deadline:
            cur = TUNER.status()
            if not (cur.get("playing") and cur.get("channel") == num):
                self._send(503, '{"error":"channel changed"}', "application/json")
                return
            if not TUNER.is_checking(num) and \
                    os.path.exists(playlist) and os.path.getsize(playlist) > 0:
                self._file(playlist)
                return
            if cur.get("playing") and not cur["signal"] and \
                    time.time() - TUNER.started_at > 6:
                break
            time.sleep(0.25)
        self._send(503, '{"error":"no signal"}', "application/json")


def cleanup(*_):
    TUNER.stop()
    sys.exit(0)


def main():
    global CHANNELS
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", help="资源根目录(打包 App 模式)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    if args.root:
        global BASE_DIR
        BASE_DIR = args.root

    m3u = os.path.join(BASE_DIR, "channels.m3u")
    CHANNELS = parse_m3u(m3u)
    if not CHANNELS:
        print("频道表为空:", m3u)
        sys.exit(1)
    load_cache()

    atexit.register(TUNER.stop)
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    httpd.daemon_threads = True
    print(f"联通电视已启动: http://localhost:{args.port}  "
          f"({len(CHANNELS)} 个频道)", flush=True)
    import socket
    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
        print(f"局域网内其他设备也可访问: http://{lan_ip}:{args.port}", flush=True)
    except OSError:
        pass
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(
            f"http://localhost:{args.port}")).start()
    start_preprobe()
    httpd.serve_forever()


if __name__ == "__main__":
    main()
