#!/bin/bash
set -e

# ============================================================
# NapCat Docker 入口脚本
# 基于 NapCat-Docker 官方 entrypoint.sh 精简
# ============================================================

# 首次运行：解压 NapCat Shell（如果 config 目录已有旧版本，跳过解压避免覆盖配置）
if [ ! -f /app/napcat/napcat.mjs ]; then
    echo "[napcat] 首次运行，解压 NapCat.Shell.zip ..."
    unzip -q /app/NapCat.Shell.zip -d /app/NapCat.Shell
    cp -rf /app/NapCat.Shell/* /app/napcat/
    rm -rf /app/NapCat.Shell
fi

# 首次运行：生成 napcat.json（如果不存在）
if [ ! -f /app/napcat/config/napcat.json ]; then
    echo "[napcat] 生成 napcat.json ..."
    mkdir -p /app/napcat/config
    cat > /app/napcat/config/napcat.json << 'NCEOF'
{
  "fileLog": true,
  "consoleLog": true,
  "fileLogLevel": "info",
  "consoleLogLevel": "info",
  "packetBackend": "auto",
  "packetServer": "",
  "o3HookMode": "auto",
  "messageEpoch": 10000
}
NCEOF
fi

# 确保 onebot11.json 存在（NapCat.Shell.zip 可能自带 napcat.json 但没有 onebot11.json）
if [ ! -f /app/napcat/config/onebot11.json ]; then
    echo "[napcat] 生成 onebot11.json（连接到 DicePP）..."
    cat > /app/napcat/config/onebot11.json << 'OBEOF'
{
  "network": {
    "httpServers": [],
    "httpSseServers": [],
    "httpClients": [],
    "websocketServers": [],
    "websocketClients": [
      {
        "enable": true,
        "name": "DicePP",
        "url": "ws://dicepp:8080/onebot/v11/ws",
        "reportSelfMessage": false,
        "messagePostFormat": "array",
        "token": "",
        "debug": false,
        "heartInterval": 30000,
        "reconnectInterval": 30000
      }
    ],
    "plugins": []
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": false,
  "parseMultMsg": false
}
OBEOF
fi

# WebUI 配置（通过环境变量覆盖）
if [ -n "${WEBUI_TOKEN}" ] && [ ! -f /app/napcat/config/webui.json ]; then
    cat > /app/napcat/config/webui.json << WEOF
{
    "host": "0.0.0.0",
    "port": 6099,
    "token": "${WEBUI_TOKEN}",
    "loginRate": 3
}
WEOF
fi

# 清理残留锁文件
rm -f /tmp/.X1-lock

# 权限
: "${NAPCAT_GID:=0}"
: "${NAPCAT_UID:=0}"
usermod -o -u "${NAPCAT_UID}" napcat 2>/dev/null || true
groupmod -o -g "${NAPCAT_GID}" napcat 2>/dev/null || true
usermod -g "${NAPCAT_GID}" napcat 2>/dev/null || true
chown -R "${NAPCAT_UID}:${NAPCAT_GID}" /app

# 启动 Xvfb（虚拟显示，QQNT 需要）
echo "[napcat] 启动 Xvfb ..."
gosu napcat Xvfb :1 -screen 0 1080x760x16 +extension GLX +render > /dev/null 2>&1 &
sleep 2

# 启动 QQ + NapCat
export FFMPEG_PATH=/usr/bin/ffmpeg
export DISPLAY=:1
cd /app/napcat

echo "[napcat] 启动 QQ + NapCat ..."
if [ -n "${ACCOUNT}" ]; then
    exec gosu napcat /opt/QQ/qq --no-sandbox -q "${ACCOUNT}"
else
    exec gosu napcat /opt/QQ/qq --no-sandbox
fi
