"""
DicePP Dashboard 通信模块。

Bot 通过 WebSocket Control Channel（``ws_client.ControlChannelClient``）
与 Dashboard 维持双向连接，替代旧的 HTTP heartbeat。

子模块:
- ``protocol``: 消息信封协议 (dicepp-control-v1)
- ``control_token``: 本地控制凭据 (data/dicepp.db)
- ``ws_client``: Bot 端 WebSocket 客户端
"""
