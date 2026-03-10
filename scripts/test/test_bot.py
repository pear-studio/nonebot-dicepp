#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DicePP Bot 集成测试脚本

模拟 OneBot V11 协议向 Bot 发送消息，验证 Bot 能否正常接收和处理。

使用方法:
    uv run python scripts/test/test_bot.py              # 自动测试
    uv run python scripts/test/test_bot.py -i           # 交互模式
    uv run python scripts/test/test_bot.py --port 8080  # 指定端口

说明:
    - HTTP 200/204 表示 Bot 成功接收并处理了消息
    - Bot 日志中的 ApiNotAvailable 是正常现象（无真实客户端接收回复）
"""

import argparse
import json
import random
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class TestCase:
    """测试用例"""
    message: str                    # 消息内容
    description: str = ""           # 测试描述
    is_group: bool = True           # 是否群消息
    

class OneBotSimulator:
    """OneBot V11 协议模拟器"""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 8080):
        self.base_url = f"http://{host}:{port}/onebot/v11/"
        self.self_id = 10001        # 模拟的 Bot QQ 号
        self.test_user_id = 12345   # 测试用户 QQ
        self.test_group_id = 67890  # 测试群号
        self.message_id = 0
    
    def _next_message_id(self) -> int:
        self.message_id += 1
        return self.message_id
    
    def _build_event(self, message: str, is_group: bool) -> dict:
        """构造 OneBot V11 消息事件"""
        event = {
            "time": int(time.time()),
            "self_id": self.self_id,
            "post_type": "message",
            "message_type": "group" if is_group else "private",
            "sub_type": "normal" if is_group else "friend",
            "message_id": self._next_message_id(),
            "user_id": self.test_user_id,
            "message": message,
            "raw_message": message,
            "font": 0,
            "sender": {
                "user_id": self.test_user_id,
                "nickname": "测试用户",
                "sex": "unknown",
                "age": 0,
            }
        }
        
        if is_group:
            event["group_id"] = self.test_group_id
            event["sender"]["card"] = "测试用户"
            event["sender"]["role"] = "member"
        
        return event
    
    def send(self, message: str, is_group: bool = True) -> Tuple[int, str]:
        """
        发送消息到 Bot
        
        Returns:
            (status_code, response_body)
            status_code: 0 表示连接失败
        """
        event = self._build_event(message, is_group)
        data = json.dumps(event).encode("utf-8")
        
        req = urllib.request.Request(
            self.base_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Self-ID": str(self.self_id),
            },
            method="POST"
        )
        
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")
        except urllib.error.URLError as e:
            return 0, str(e.reason)
        except Exception as e:
            return 0, str(e)
    
    def check_connection(self) -> bool:
        """检查服务器是否可连接"""
        try:
            # 尝试连接根路径
            url = self.base_url.rsplit("/onebot", 1)[0]
            req = urllib.request.Request(url, method="GET")
            urllib.request.urlopen(req, timeout=5)
            return True
        except urllib.error.HTTPError:
            # HTTP 错误也说明服务器在运行
            return True
        except:
            return False


# 预定义测试用例
DEFAULT_TESTS: List[TestCase] = [
    TestCase(".help", "帮助命令", is_group=False),
    TestCase(".r", "基础骰子"),
    TestCase(".rd20", "D20 骰子"),
    TestCase(".r2d6+3", "2D6+3 骰子"),
    TestCase(".bot", "Bot 状态"),
]


def run_tests(sim: OneBotSimulator, tests: List[TestCase]) -> bool:
    """运行自动化测试"""
    print("=" * 60)
    print(" DicePP Bot 集成测试")
    print("=" * 60)
    print()
    
    # 检查连接
    print("[检查] 连接 Bot 服务器...")
    if not sim.check_connection():
        print("[失败] 无法连接，请确保 Bot 已启动")
        return False
    print("[成功] 服务器已连接")
    print()
    
    # 运行测试
    print(f"[测试] 共 {len(tests)} 个用例")
    print("-" * 40)
    
    passed = 0
    failed = 0
    
    for test in tests:
        ctx = "群聊" if test.is_group else "私聊"
        desc = test.description or test.message
        
        print(f"\n  [{ctx}] {test.message}")
        print(f"         {desc}")
        
        status, body = sim.send(test.message, test.is_group)
        
        if status in (200, 204):
            print(f"         ✅ HTTP {status}")
            passed += 1
        elif status == 0:
            print(f"         ❌ 连接失败: {body}")
            failed += 1
        else:
            print(f"         ❌ HTTP {status}")
            failed += 1
        
        time.sleep(0.3)  # 避免发送过快
    
    # 汇总
    print()
    print("=" * 60)
    print(f" 结果: {passed} 通过, {failed} 失败")
    print("=" * 60)
    print()
    
    if failed == 0:
        print("🎉 所有测试通过!")
        print()
        print("📝 说明:")
        print("   - HTTP 200/204 表示 Bot 成功处理了消息")
        print("   - Bot 日志中的 ApiNotAvailable 是正常现象")
        print("   - 连接真实聊天客户端后即可正常收发消息")
    
    return failed == 0


def interactive_mode(sim: OneBotSimulator):
    """交互模式"""
    print("=" * 60)
    print(" DicePP Bot 交互测试")
    print("=" * 60)
    print()
    print("命令:")
    print("  直接输入消息发送到 Bot")
    print("  /g <消息>  - 发送群消息 (默认)")
    print("  /p <消息>  - 发送私聊消息")
    print("  /quit      - 退出")
    print()
    print("=" * 60)
    print()
    
    # 检查连接
    if not sim.check_connection():
        print("[失败] 无法连接，请确保 Bot 已启动")
        return
    print("[成功] 已连接到 Bot")
    print()
    
    while True:
        try:
            line = input("[输入] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见!")
            break
        
        if not line:
            continue
        
        if line.lower() in ("/quit", "/exit", "quit", "exit"):
            print("再见!")
            break
        
        # 解析消息类型
        is_group = True
        message = line
        
        if line.startswith("/p "):
            is_group = False
            message = line[3:]
        elif line.startswith("/g "):
            message = line[3:]
        
        # 发送
        ctx = "群聊" if is_group else "私聊"
        status, body = sim.send(message, is_group)
        
        if status in (200, 204):
            print(f"[回应] ✅ HTTP {status} ({ctx})")
        elif status == 0:
            print(f"[回应] ❌ 连接失败: {body}")
        else:
            print(f"[回应] ❌ HTTP {status}: {body[:100]}")
        
        # 显示响应内容
        if body and body not in ("{}", ""):
            try:
                data = json.loads(body)
                if data:
                    print(f"        {json.dumps(data, ensure_ascii=False)}")
            except:
                pass
        print()


def main():
    parser = argparse.ArgumentParser(description="DicePP Bot 集成测试")
    parser.add_argument("--host", default="127.0.0.1", help="Bot 地址")
    parser.add_argument("--port", "-p", type=int, default=8080, help="Bot 端口")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互模式")
    
    args = parser.parse_args()
    
    sim = OneBotSimulator(host=args.host, port=args.port)
    
    if args.interactive:
        interactive_mode(sim)
        return 0
    else:
        success = run_tests(sim, DEFAULT_TESTS)
        return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
