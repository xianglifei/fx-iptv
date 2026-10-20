#!/bin/zsh
# 备用启动方式:终端窗口运行服务并自动打开浏览器
cd "$(dirname "$0")"
exec python3 server.py
