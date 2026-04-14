import sqlite3
import json
import os
from datetime import datetime
from typing import Any

os.makedirs("data", exist_ok=True)
DB_PATH = "data/data.db"

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def init_db():
    """初始化 SQLite 数据库，创建账号存储表"""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        c.execute('PRAGMA journal_mode=WAL;')
        c.execute('PRAGMA synchronous=NORMAL;')
        c.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                password TEXT,
                token_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('''
                    CREATE TABLE IF NOT EXISTS system_kv (
                        key TEXT PRIMARY KEY, 
                        value TEXT
                    )
                ''')
        c.execute('''
                    CREATE TABLE IF NOT EXISTS local_mailboxes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        email TEXT UNIQUE,
                        password TEXT,
                        client_id TEXT,
                        refresh_token TEXT,
                        status INTEGER DEFAULT 0,  -- 0:未用, 1:被占用, 2:已出凭证, 3:死号
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
        try:
            c.execute('ALTER TABLE local_mailboxes ADD COLUMN fission_count INTEGER DEFAULT 0;')
            c.execute('ALTER TABLE local_mailboxes ADD COLUMN retry_master INTEGER DEFAULT 0;')
        except sqlite3.OperationalError:
            pass
        conn.commit()
    print(f"[{ts()}] [系统] 数据库模块初始化完成")

def save_account_to_db(email: str, password: str, token_json_str: str) -> bool:
    """账号、密码和 Token 数据存入数据库"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO accounts (email, password, token_data)
                VALUES (?, ?, ?)
            ''', (email, password, token_json_str))
            conn.commit()
            return True
    except Exception as e:
        print(f"[{ts()}] [ERROR] 数据库保存失败: {e}")
        return False

def get_all_accounts() -> list:
    """获取所有账号列表，按最新时间倒序"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT email, password, created_at FROM accounts ORDER BY id DESC")
            rows = c.fetchall()
            return [{"email": r[0], "password": r[1], "created_at": r[2]} for r in rows]
    except Exception as e:
        print(f"[{ts()}] [ERROR] 获取账号列表失败: {e}")
        return []

def get_token_by_email(email: str) -> dict:
    """根据邮箱提取完整的 Token JSON 数据（用于推送）"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT token_data FROM accounts WHERE email = ?", (email,))
            row = c.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return None
    except Exception as e:
        print(f"[{ts()}] [ERROR] 读取 Token 失败: {e}")
        return None

def get_tokens_by_emails(emails: list) -> list:
    """根据前端传入的邮箱列表，提取 Token"""
    if not emails:
        return []
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            placeholders = ','.join(['?'] * len(emails))
            c.execute(f"SELECT token_data FROM accounts WHERE email IN ({placeholders})", emails)
            rows = c.fetchall()
            
            export_list = []
            for r in rows:
                if r[0]:
                    try:
                        export_list.append(json.loads(r[0]))
                    except:
                        pass
            return export_list
    except Exception as e:
        return []
        
def delete_accounts_by_emails(emails: list) -> bool:
    """批量从数据库中删除账号"""
    if not emails:
        return True
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            placeholders = ','.join(['?'] * len(emails))
            c.execute(f"DELETE FROM accounts WHERE email IN ({placeholders})", emails)
            conn.commit()
            return True
    except Exception as e:
        print(f"[{ts()}] [ERROR] 数据库批量删除账号异常: {e}")
        return False

def get_accounts_page(page: int = 1, page_size: int = 50) -> dict:
    """带分页的账号拉取功能"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(1) FROM accounts")
            total = c.fetchone()[0]

            offset = (page - 1) * page_size
            c.execute("SELECT email, password, created_at FROM accounts ORDER BY id DESC LIMIT ? OFFSET ?", (page_size, offset))
            rows = c.fetchall()
            
            data = [{"email": r[0], "password": r[1], "created_at": r[2]} for r in rows]
            return {"total": total, "data": data}
    except Exception as e:
        print(f"[{ts()}] [ERROR] 分页获取账号列表失败: {e}")
        return {"total": 0, "data": []}

def set_sys_kv(key: str, value: Any):
    """保存任意数据到系统表"""
    try:
        val_str = json.dumps(value, ensure_ascii=False)
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("INSERT OR REPLACE INTO system_kv (key, value) VALUES (?, ?)", (key, val_str))
            conn.commit()
    except Exception as e:
        print(f"[{ts()}] [ERROR] 系统配置保存失败: {e}")

def get_sys_kv(key: str, default=None):
    """从系统表读取数据"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            cursor = conn.execute("SELECT value FROM system_kv WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
    except Exception:
        pass
    return default


def get_all_accounts_with_token(limit: int = 10000) -> list:
    """提取包含完整 token_data 的账号列表，专门用于集群导出"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT email, password, token_data FROM accounts ORDER BY id DESC LIMIT ?", (limit,))
            rows = c.fetchall()

            return [{"email": r[0], "password": r[1], "token_data": r[2]} for r in rows]
    except Exception as e:
        print(f"[{ts()}] [ERROR] 提取完整账号数据失败: {e}")
        return []

def save_account_to_db(email: str, password: str, token_json_str: str) -> bool:
    """账号、密码和 Token 数据存入数据库"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute('''
                INSERT OR IGNORE INTO accounts (email, password, token_data)
                VALUES (?, ?, ?)
            ''', (email, password, token_json_str))
            conn.commit()
            return True
    except Exception as e:
        print(f"[{ts()}] [ERROR] 数据库保存失败: {e}")
        return False

def import_local_mailboxes(mailboxes_data: list) -> int:
    count = 0
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            for mb in mailboxes_data:
                try:
                    c.execute('''
                        INSERT OR IGNORE INTO local_mailboxes (email, password, client_id, refresh_token, status)
                        VALUES (?, ?, ?, ?, 0)
                    ''', (mb['email'], mb['password'], mb.get('client_id', ''), mb.get('refresh_token', '')))
                    if c.rowcount > 0:
                        count += 1
                except:
                    pass
            conn.commit()
    except Exception as e:
        print(f"[{ts()}] [ERROR] 导入邮箱库失败: {e}")
    return count

def get_local_mailboxes_page(page: int = 1, page_size: int = 50) -> dict:
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT COUNT(1) FROM local_mailboxes")
            total = c.fetchone()[0]

            offset = (page - 1) * page_size
            c.execute("SELECT * FROM local_mailboxes ORDER BY id DESC LIMIT ? OFFSET ?", (page_size, offset))
            rows = c.fetchall()
            return {"total": total, "data": [dict(r) for r in rows]}
    except Exception as e:
        return {"total": 0, "data": []}

def delete_local_mailboxes(ids: list) -> bool:
    if not ids: return True
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            placeholders = ','.join(['?'] * len(ids))
            c.execute(f"DELETE FROM local_mailboxes WHERE id IN ({placeholders})", ids)
            conn.commit()
            return True
    except Exception as e:
        return False

def get_and_lock_unused_local_mailbox() -> dict:
    """提取一个未使用的账号，并状态锁定为 1 (占用中)，防止并发撞车"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("BEGIN EXCLUSIVE")
            c.execute("SELECT * FROM local_mailboxes WHERE status = 0 ORDER BY id ASC LIMIT 1")
            row = c.fetchone()
            if row:
                c.execute("UPDATE local_mailboxes SET status = 1 WHERE id = ?", (row['id'],))
                conn.commit()
                return dict(row)
            conn.commit()
            return None
    except Exception as e:
        print(f"[{ts()}] [ERROR] 提取本地邮箱失败: {e}")
        return None

def update_local_mailbox_status(email: str, status: int):
    """更新邮箱状态：1=占用 2=出凭证 3=死号"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("UPDATE local_mailboxes SET status = ? WHERE email = ?", (status, email))
            conn.commit()
    except Exception: pass

def update_local_mailbox_refresh_token(email: str, new_rt: str):
    """刷新了 Token 后更新数据库"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("UPDATE local_mailboxes SET refresh_token = ? WHERE email = ?", (new_rt, email))
            conn.commit()
    except Exception: pass

def get_and_lock_unused_local_mailbox() -> dict:
    """不分裂"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("BEGIN EXCLUSIVE")
            c.execute("SELECT * FROM local_mailboxes WHERE status = 0 ORDER BY id ASC LIMIT 1")
            row = c.fetchone()
            if row:
                c.execute("UPDATE local_mailboxes SET status = 1 WHERE id = ?", (row['id'],))
                conn.commit()
                return dict(row)
            conn.commit()
    except Exception as e: print(f"[{ts()}] [ERROR] {e}")
    return None


def get_mailbox_for_pool_fission() -> dict:
    """
    提取的同时立刻增加计数，防止多线程撞车
    """
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("BEGIN EXCLUSIVE")
            c.execute("SELECT * FROM local_mailboxes WHERE status = 0 AND retry_master = 1 LIMIT 1")
            row = c.fetchone()

            if not row:
                c.execute("SELECT * FROM local_mailboxes WHERE status = 0 ORDER BY fission_count ASC LIMIT 1")
                row = c.fetchone()

            if row:
                c.execute("UPDATE local_mailboxes SET fission_count = fission_count + 1 WHERE id = ?", (row['id'],))
                conn.commit()
                return dict(row)

            conn.commit()
            return None
    except Exception as e:
        print(f"[{ts()}] [DB_ERROR] 提取失败: {e}")
        return None

def update_pool_fission_result(email: str, is_blocked: bool, is_raw: bool):
    """
    处理库分裂结果：
    """
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            if not is_blocked:
                conn.execute("UPDATE local_mailboxes SET retry_master = 0 WHERE email = ?", (email,))
            else:
                if not is_raw:
                    conn.execute("UPDATE local_mailboxes SET retry_master = 1 WHERE email = ?", (email,))
                else:
                    conn.execute("UPDATE local_mailboxes SET status = 3, retry_master = 0 WHERE email = ?", (email,))
            conn.commit()
    except Exception as e:
        print(f"[{ts()}] [DB_ERROR] 结果更新失败: {e}")

def clear_retry_master_status(email: str):
    """
    清除邮箱的母号重试标记，防止多线程并发时重复取号
    """
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("UPDATE local_mailboxes SET retry_master = 0 WHERE email = ?", (email,))
            conn.commit()
    except Exception as e:
        print(f"[{ts()}] [DB_ERROR] 清除 {email} 的 retry_master 状态失败: {e}")