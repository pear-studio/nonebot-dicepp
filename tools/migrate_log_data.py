#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DicePP 日志数据内存清理脚本

用途：清理 log_session.json 中膨胀的历史数据，降低内存占用。

操作说明：
1. 确保机器人已停止运行
2. 运行此脚本: python tools/migrate_log_data.py
3. 脚本会自动扫描 Data/Bot 目录下所有账号的日志数据
4. 清理完成后重启机器人
"""

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

# 项目路径设置
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_PATH = os.path.join(PROJECT_ROOT, "src", "plugins", "DicePP")
DATA_PATH = os.path.join(SRC_PATH, "Data", "Bot")

sys.path.insert(0, SRC_PATH)

# 尝试导入日志数据库模块（用于迁移 records 到 SQLite）
try:
    from module.common.log_db import get_connection, insert_record, upsert_log
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    print("[WARN] 无法导入 log_db 模块，records 将仅被清理而不迁移到数据库")

# 限制常量（与 log_command.py 保持一致）
LOG_PARTICIPANTS_LIMIT = 500
LOG_COLOR_MAP_LIMIT = 500
LOG_DICE_USERS_LIMIT = 100
LOG_IN_MEMORY_RECORDS_LIMIT = 50


def backup_file(filepath: str) -> str:
    """备份原文件"""
    backup_path = f"{filepath}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return backup_path


def trim_participants(stats: Dict[str, Any]) -> int:
    """裁剪 participants，返回清理的条目数"""
    participants = stats.get("participants")
    if not isinstance(participants, dict):
        return 0
    original_count = len(participants)
    if original_count <= LOG_PARTICIPANTS_LIMIT:
        return 0
    sorted_items = sorted(
        participants.items(),
        key=lambda x: x[1].get("count", 0) if isinstance(x[1], dict) else 0,
        reverse=True
    )[:LOG_PARTICIPANTS_LIMIT]
    stats["participants"] = dict(sorted_items)
    return original_count - LOG_PARTICIPANTS_LIMIT


def trim_dice_faces_users(stats: Dict[str, Any]) -> int:
    """裁剪 dice_faces.users，返回清理的条目数"""
    dice_faces = stats.get("dice_faces")
    if not isinstance(dice_faces, dict):
        return 0
    total_trimmed = 0
    for face, face_info in dice_faces.items():
        if not isinstance(face_info, dict):
            continue
        users = face_info.get("users")
        if isinstance(users, dict) and len(users) > LOG_DICE_USERS_LIMIT:
            original_count = len(users)
            sorted_users = sorted(
                users.items(),
                key=lambda x: x[1].get("count", 0) if isinstance(x[1], dict) else 0,
                reverse=True
            )[:LOG_DICE_USERS_LIMIT]
            face_info["users"] = dict(sorted_users)
            total_trimmed += original_count - LOG_DICE_USERS_LIMIT
    return total_trimmed


def trim_color_map(entry: Dict[str, Any]) -> int:
    """裁剪 color_map，返回清理的条目数"""
    color_map = entry.get("color_map")
    if not isinstance(color_map, dict):
        return 0
    original_count = len(color_map)
    if original_count <= LOG_COLOR_MAP_LIMIT:
        return 0
    
    # 优先保留 participants 中存在的用户
    stats = entry.get("stats", {})
    participants = stats.get("participants", {}) if isinstance(stats, dict) else {}
    active_users = set(participants.keys())

    new_map = {}
    for uid in active_users:
        if uid in color_map and len(new_map) < LOG_COLOR_MAP_LIMIT:
            new_map[uid] = color_map[uid]
    for uid, color in color_map.items():
        if uid not in new_map and len(new_map) < LOG_COLOR_MAP_LIMIT:
            new_map[uid] = color

    entry["color_map"] = new_map
    return original_count - len(new_map)


def migrate_records_to_db(group_id: str, log_id: str, log_entry: Dict[str, Any], records: List[Dict]) -> int:
    """将 records 迁移到 SQLite 数据库，返回迁移的记录数"""
    if not DB_AVAILABLE or not records:
        return 0
    
    try:
        conn = get_connection()
        try:
            # 先确保日志元数据存在
            upsert_log(conn, {
                "id": log_id,
                "group_id": group_id,
                "name": log_entry.get("name", log_id),
                "created_at": log_entry.get("created_at", ""),
                "updated_at": log_entry.get("updated_at", ""),
                "recording": log_entry.get("recording", False),
                "record_begin_at": log_entry.get("record_begin_at", ""),
                "last_warn": log_entry.get("last_warn", ""),
                "filter_outside": 0,
                "filter_command": 0,
                "filter_bot": 0,
                "filter_media": 0,
                "filter_forum_code": 0,
            })
            
            # 迁移每条记录
            migrated = 0
            for record in records:
                try:
                    insert_record(
                        conn, log_id,
                        time=record.get("time", ""),
                        user_id=str(record.get("user_id", "")),
                        nickname=record.get("nickname", ""),
                        content=record.get("content", ""),
                        source=record.get("source", "user"),
                        message_id=record.get("message_id"),
                    )
                    migrated += 1
                except Exception as e:
                    # 可能是重复的记录，忽略
                    pass
            conn.commit()
            return migrated
        finally:
            conn.close()
    except Exception as e:
        print(f"  [ERROR] 迁移 records 失败: {e}")
        return 0


def trim_records(entry: Dict[str, Any]) -> int:
    """裁剪 records 列表，返回清理的条目数"""
    records = entry.get("records")
    if not isinstance(records, list):
        return 0
    original_count = len(records)
    if original_count <= LOG_IN_MEMORY_RECORDS_LIMIT:
        return 0
    # 保留最新的 N 条
    entry["records"] = records[-LOG_IN_MEMORY_RECORDS_LIMIT:]
    return original_count - LOG_IN_MEMORY_RECORDS_LIMIT


def process_log_session(filepath: str) -> Dict[str, int]:
    """处理单个 log_session.json 文件"""
    stats = {
        "groups_processed": 0,
        "logs_processed": 0,
        "records_migrated": 0,
        "records_trimmed": 0,
        "participants_trimmed": 0,
        "dice_users_trimmed": 0,
        "color_map_trimmed": 0,
    }
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"  [ERROR] 无法读取文件: {e}")
        return stats
    
    if not isinstance(data, dict):
        print(f"  [WARN] 文件格式不正确，跳过")
        return stats
    
    # 遍历所有群组
    for group_id, group_data in data.items():
        if not isinstance(group_data, dict):
            continue
        stats["groups_processed"] += 1
        
        logs = group_data.get("logs", {})
        if not isinstance(logs, dict):
            continue
        
        for log_id, log_entry in logs.items():
            if not isinstance(log_entry, dict):
                continue
            stats["logs_processed"] += 1
            
            # 1. 迁移 records 到数据库
            records = log_entry.get("records", [])
            if isinstance(records, list) and len(records) > LOG_IN_MEMORY_RECORDS_LIMIT:
                migrated = migrate_records_to_db(group_id, log_id, log_entry, records)
                stats["records_migrated"] += migrated
            
            # 2. 裁剪 records
            stats["records_trimmed"] += trim_records(log_entry)
            
            # 3. 裁剪 stats
            entry_stats = log_entry.get("stats", {})
            if isinstance(entry_stats, dict):
                stats["participants_trimmed"] += trim_participants(entry_stats)
                stats["dice_users_trimmed"] += trim_dice_faces_users(entry_stats)
            
            # 4. 裁剪 color_map
            stats["color_map_trimmed"] += trim_color_map(log_entry)
    
    # 保存处理后的数据
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  [OK] 文件已更新")
    except Exception as e:
        print(f"  [ERROR] 无法保存文件: {e}")
    
    return stats


def main():
    print("=" * 60)
    print("DicePP 日志数据内存清理脚本")
    print("=" * 60)
    print()
    
    if not os.path.exists(DATA_PATH):
        print(f"[ERROR] 数据目录不存在: {DATA_PATH}")
        print("请确保从项目根目录运行此脚本")
        return
    
    total_stats = {
        "files_processed": 0,
        "groups_processed": 0,
        "logs_processed": 0,
        "records_migrated": 0,
        "records_trimmed": 0,
        "participants_trimmed": 0,
        "dice_users_trimmed": 0,
        "color_map_trimmed": 0,
    }
    
    # 扫描所有 Bot 账号目录
    for account_dir in os.listdir(DATA_PATH):
        account_path = os.path.join(DATA_PATH, account_dir)
        if not os.path.isdir(account_path):
            continue
        
        log_session_path = os.path.join(account_path, "log_session.json")
        if not os.path.exists(log_session_path):
            continue
        
        print(f"\n处理账号: {account_dir}")
        print(f"  文件: {log_session_path}")
        
        # 获取文件大小
        file_size = os.path.getsize(log_session_path)
        print(f"  原始大小: {file_size / 1024:.1f} KB")
        
        # 备份
        backup_path = backup_file(log_session_path)
        print(f"  备份到: {backup_path}")
        
        # 处理
        stats = process_log_session(log_session_path)
        
        # 获取新文件大小
        new_size = os.path.getsize(log_session_path)
        print(f"  新大小: {new_size / 1024:.1f} KB (节省 {(file_size - new_size) / 1024:.1f} KB)")
        
        # 汇总统计
        total_stats["files_processed"] += 1
        for key in stats:
            total_stats[key] += stats[key]
    
    # 打印总结
    print("\n" + "=" * 60)
    print("清理完成！统计信息：")
    print("=" * 60)
    print(f"  处理文件数: {total_stats['files_processed']}")
    print(f"  处理群组数: {total_stats['groups_processed']}")
    print(f"  处理日志数: {total_stats['logs_processed']}")
    print(f"  迁移记录数: {total_stats['records_migrated']}")
    print(f"  清理记录数: {total_stats['records_trimmed']}")
    print(f"  清理参与者: {total_stats['participants_trimmed']}")
    print(f"  清理骰面用户: {total_stats['dice_users_trimmed']}")
    print(f"  清理颜色映射: {total_stats['color_map_trimmed']}")
    print()
    print("请重启机器人以应用更改。")


if __name__ == "__main__":
    main()
