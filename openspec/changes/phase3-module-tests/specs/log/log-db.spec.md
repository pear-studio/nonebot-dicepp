# Spec: Log 数据库接口规格

## 概述

定义日志系统 SQLite 数据库操作接口。

## 源文件

**位置**: `src/plugins/DicePP/module/common/log_db.py`

## 数据库结构

### logs 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PRIMARY KEY | 日志唯一ID |
| `group_id` | TEXT NOT NULL | 群ID |
| `name` | TEXT NOT NULL | 日志名称 |
| `created_at` | TEXT NOT NULL | 创建时间 |
| `updated_at` | TEXT NOT NULL | 更新时间 |
| `recording` | INTEGER (0/1) | 是否正在记录 |
| `record_begin_at` | TEXT NOT NULL | 本次记录开始时间 |
| `last_warn` | TEXT NOT NULL | 上次警告时间 |
| `filter_outside` | INTEGER (0/1) | 过滤非游戏内容 |
| `filter_command` | INTEGER (0/1) | 过滤命令消息 |
| `filter_bot` | INTEGER (0/1) | 过滤机器人消息 |
| `filter_media` | INTEGER (0/1) | 过滤媒体消息 |
| `filter_forum_code` | INTEGER (0/1) | 过滤论坛代码 |
| `upload_time` | TEXT | 上传时间 |
| `upload_file` | TEXT | 上传文件名 |
| `upload_note` | TEXT | 上传备注 |
| `url` | TEXT | 外部链接 |

### records 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY | 自增ID |
| `log_id` | TEXT NOT NULL | 关联日志ID (FK) |
| `time` | TEXT NOT NULL | 消息时间 |
| `user_id` | TEXT NOT NULL | 用户ID |
| `nickname` | TEXT | 用户昵称 |
| `content` | TEXT NOT NULL | 消息内容 |
| `source` | TEXT NOT NULL | 来源 ("bot" / "user") |
| `message_id` | TEXT | 消息ID（用于撤回） |

## 函数规格

### SPEC-P3-L001: get_connection

**签名**: `def get_connection() -> sqlite3.Connection`

**行为**:
1. 确保日志目录存在
2. 如果数据库文件不存在，创建并初始化 schema
3. 设置 PRAGMA（WAL, NORMAL sync, FK ON）
4. 返回连接对象

**验收标准**:
- 首次调用创建数据库文件和表
- 多次调用幂等
- 返回的连接可以正常执行 SQL

### SPEC-P3-L010: insert_record

**签名**:
```python
def insert_record(
    conn: sqlite3.Connection, 
    log_id: str, 
    *, 
    time: str, 
    user_id: str,
    nickname: str, 
    content: str, 
    source: str, 
    message_id: Optional[str]
) -> None
```

**行为**:
- 向 records 表插入一条记录

**验收标准**:
- 插入后 `fetch_records` 能返回该记录
- `source` 应为 "bot" 或 "user"

### SPEC-P3-L011: fetch_records

**签名**: `def fetch_records(conn: sqlite3.Connection, log_id: str) -> List[Dict[str, Any]]`

**行为**:
- 返回指定日志的所有记录
- 按 id 升序排列

**返回字段**:
- `time`, `user_id`, `nickname`, `content`, `source`, `message_id`

### SPEC-P3-L012: delete_records_by_message_id

**签名**: `def delete_records_by_message_id(conn: sqlite3.Connection, log_id: str, message_id: str) -> int`

**行为**:
- 删除指定日志中 message_id 匹配的记录
- 返回删除的行数

### SPEC-P3-L013: delete_records_for_log

**签名**: `def delete_records_for_log(conn: sqlite3.Connection, log_id: str) -> None`

**行为**:
- 删除指定日志的所有记录

### SPEC-P3-L020: upsert_log

**签名**: `def upsert_log(conn: sqlite3.Connection, payload: Dict[str, Any]) -> None`

**行为**:
- 如果 id 不存在，插入新日志
- 如果 id 存在，更新所有字段
- 幂等操作

**必需字段**:
- `id`, `group_id`, `name`, `created_at`, `updated_at`, `record_begin_at`, `last_warn`

### SPEC-P3-L021: get_log_by_id

**签名**: `def get_log_by_id(conn: sqlite3.Connection, log_id: str) -> Optional[Dict[str, Any]]`

**行为**:
- 返回指定 ID 的日志元数据
- 不存在返回 None

### SPEC-P3-L022: get_logs_by_group

**签名**: `def get_logs_by_group(conn: sqlite3.Connection, group_id: str) -> List[Dict[str, Any]]`

**行为**:
- 返回指定群的所有日志
- 按 created_at 升序

### SPEC-P3-L023: get_log_id_by_name

**签名**: `def get_log_id_by_name(conn: sqlite3.Connection, group_id: str, name: str) -> Optional[str]`

**行为**:
- 根据群ID和日志名称查找日志ID
- **大小写不敏感**

### SPEC-P3-L024: set_recording

**签名**: `def set_recording(conn: sqlite3.Connection, log_id: str, recording: bool) -> None`

**行为**:
- 设置日志的 recording 状态

### SPEC-P3-L025: delete_log

**签名**: `def delete_log(conn: sqlite3.Connection, log_id: str) -> None`

**行为**:
- 删除日志元数据
- 由于 FK 级联，关联记录自动删除

## 测试用例规格

### TC-L001: 插入和查询记录

```python
def test_insert_and_fetch(conn):
    # 先创建日志
    upsert_log(conn, {
        "id": "log_001", "group_id": "g1", "name": "测试",
        "created_at": "2026-01-01", "updated_at": "2026-01-01",
        "record_begin_at": "", "last_warn": ""
    })
    conn.commit()
    
    insert_record(conn, "log_001",
        time="2026-01-01 00:00:00", user_id="u1",
        nickname="Alice", content="Hello", source="user", message_id="m1"
    )
    conn.commit()
    
    records = fetch_records(conn, "log_001")
    assert len(records) == 1
    assert records[0]["content"] == "Hello"
    assert records[0]["user_id"] == "u1"
```

### TC-L002: 按 message_id 删除

```python
def test_delete_by_message_id(conn):
    # 设置同上
    insert_record(conn, "log_001", time="t", user_id="u1",
        nickname="A", content="X", source="user", message_id="m1")
    insert_record(conn, "log_001", time="t", user_id="u1",
        nickname="A", content="Y", source="user", message_id="m2")
    conn.commit()
    
    deleted = delete_records_by_message_id(conn, "log_001", "m1")
    conn.commit()
    
    assert deleted == 1
    records = fetch_records(conn, "log_001")
    assert len(records) == 1
    assert records[0]["message_id"] == "m2"
```

### TC-L003: upsert 幂等性

```python
def test_upsert_idempotent(conn):
    payload = {
        "id": "log_001", "group_id": "g1", "name": "初始名",
        "created_at": "t1", "updated_at": "t1",
        "record_begin_at": "", "last_warn": ""
    }
    upsert_log(conn, payload)
    conn.commit()
    
    # 更新名称
    payload["name"] = "新名称"
    upsert_log(conn, payload)
    conn.commit()
    
    result = get_log_by_id(conn, "log_001")
    assert result["name"] == "新名称"
    
    # 只有一条记录
    all_logs = get_logs_by_group(conn, "g1")
    assert len(all_logs) == 1
```

### TC-L004: 按名称查找（大小写不敏感）

```python
def test_get_by_name_case_insensitive(conn):
    upsert_log(conn, {
        "id": "log_abc", "group_id": "g1", "name": "MyLog",
        "created_at": "t", "updated_at": "t",
        "record_begin_at": "", "last_warn": ""
    })
    conn.commit()
    
    assert get_log_id_by_name(conn, "g1", "mylog") == "log_abc"
    assert get_log_id_by_name(conn, "g1", "MYLOG") == "log_abc"
    assert get_log_id_by_name(conn, "g1", "MyLog") == "log_abc"
```

### TC-L005: 级联删除

```python
def test_cascade_delete(conn):
    upsert_log(conn, {"id": "log_001", "group_id": "g1", "name": "X", ...})
    insert_record(conn, "log_001", time="t", user_id="u1",
        nickname="A", content="Y", source="user", message_id=None)
    conn.commit()
    
    delete_log(conn, "log_001")
    conn.commit()
    
    assert get_log_by_id(conn, "log_001") is None
    assert fetch_records(conn, "log_001") == []
```

## pytest Fixture

```python
@pytest.fixture
def log_conn(tmp_path, monkeypatch):
    """创建临时 SQLite 连接"""
    import sqlite3
    from module.common import log_db
    
    db_path = tmp_path / "test_log.db"
    
    # Patch 路径常量
    monkeypatch.setattr(log_db, "LOG_DB_PATH", str(db_path))
    monkeypatch.setattr(log_db, "LOG_DIR", str(tmp_path))
    
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    log_db._init_schema(conn)
    
    yield conn
    
    conn.close()
```
