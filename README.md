# 联通电视 — Mac 本地 IPTV 机顶盒

北京联通 IPTV 组播直播在 Mac 上的机顶盒式播放器:频道墙点选 + 数字键选台。

## 启动

- **推荐**:双击 `联通电视.app`(独立窗口,有 Dock 图标)
- **备用**:双击 `启动联通电视.command`(终端 + 浏览器)

服务监听 `http://localhost:8787`,局域网内 iPhone/iPad 也可直接访问 `http://<Mac的IP>:8787` 观看。

## 操作(机顶盒交互)

| 操作 | 效果 |
|---|---|
| 点击频道卡片 | 观看该频道 |
| 数字键 0-9 | 屏幕出现大台号,1.2 秒后自动跳台 |
| ↓ / ↑ | 下一个 / 上一个频道 |
| F | 全屏 |
| Esc | 返回频道墙 |

## 工作原理

```
rtp://239.3.1.x 组播 ──> ffmpeg(隔行:yadif反交错+VideoToolbox硬编25p / 逐行:copy)
                                                      │
              浏览器/App 窗口 <── hls.js <── HLS 1秒分片 + 本地HTTP(localhost:8787)
```

- 联通高清源为 **1080i 隔行 + MP2 音频**:隔行必须反交错(否则网页播放抖动拉丝),音频必须转 AAC(否则黑屏);
- **隔行属性自动探测并持久化**(首次播放时探测首个分片,启动时还会后台预探测"最近观看+常用前12"),结果存于 `~/.mac-iptv/field_cache.json`,重启不丢;
- 首次接入隔行频道约 5~6 秒(探测+切管线),之后 2~3 秒;逐行频道始终 2~3 秒;
- 同一时刻只有一个 ffmpeg 实例(和真机顶盒一致),换台自动切换;
- 退出程序自动清理 ffmpeg 进程。

## 依赖

- macOS 12+,Python 3(GUI App 内置了路径记录,终端方式需 `python3` 在 PATH)
- ffmpeg(Homebrew 安装即可)
- 仅限北京联通宽带网络内使用(组播不出公网)

## 文件说明

- `server.py` — 后端:频道解析、ffmpeg 换台、HLS 服务
- `channels.m3u` — 频道表(来源:wuwentao/bj-unicom-iptv)
- `static/` — 前端(频道墙 UI)
- `main.swift` + `build_app.sh` — 独立 App 壳与构建脚本(`./build_app.sh` 重新打包)
