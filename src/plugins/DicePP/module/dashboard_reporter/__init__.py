"""
DicePP Dashboard 通信模块。

Bot 通过 WebSocket Control Channel（``ws_client.ControlChannelClient``）
与 Dashboard 维持双向连接，替代旧的 HTTP heartbeat。

消息信封协议 (dicepp-control-v1) 与 Bot↔Manager 控制凭据已迁至无副作用的
共享包 ``dicepp_control``，供 Bot、Manager、Dashboard 三方共同 import。

子模块:
- ``ws_client``: Bot 端 WebSocket 客户端
"""
