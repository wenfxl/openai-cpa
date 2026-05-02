import sqlite3
import pymysql
import json
import datetime
import os
import threading
from typing import Any
from utils.config import DB_TYPE, MYSQL_CFG
from utils import config as cfg

os.makedirs("data", exist_ok=True)
DB_PATH = "data/data.db"
_team_db_lock = threading.Lock()

class get_db_conn:
    """抹平 SQLite 和 MySQL 连接差异"""
    def __init__(self, as_dict=False):
        self.as_dict = as_dict

    def __enter__(self):
        if DB_TYPE == "mysql":
            self.conn = pymysql.connect(
                host=MYSQL_CFG.get('host', '127.0.0.1'),
                port=MYSQL_CFG.get('port', 3306),
                user=MYSQL_CFG.get('user', 'root'),
                password=MYSQL_CFG.get('password', ''),
                database=MYSQL_CFG.get('db_name', 'wenfxl_manager'),
                charset='utf8mb4'
            )
        else:
            self.conn = sqlite3.connect(DB_PATH, timeout=10)
            if self.as_dict:
                self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.conn.close()


def get_cursor(conn, as_dict=False):
    """获取适配的游标"""
    if DB_TYPE == "mysql" and as_dict:
        return conn.cursor(pymysql.cursors.DictCursor)
    return conn.cursor()


def execute_sql(cursor, sql: str, params=()):
    if DB_TYPE == "mysql":
        sql = sql.replace('?', '%s')
        sql = sql.replace('AUTOINCREMENT', 'AUTO_INCREMENT')

        sql = sql.replace('INSERT OR IGNORE', 'INSERT IGNORE')
        sql = sql.replace('INSERT OR REPLACE', 'REPLACE')

        sql = sql.replace('TEXT UNIQUE', 'VARCHAR(191) UNIQUE')
        sql = sql.replace('TEXT PRIMARY KEY', 'VARCHAR(191) PRIMARY KEY')

        # 3. 抹平特殊的 PRAGMA
        if 'PRAGMA' in sql:
            return None

    return cursor.execute(sql, params)

def init_db():
    """初始化数据库，自动适应双引擎建表"""
    with get_db_conn() as conn:
        c = get_cursor(conn)
        execute_sql(c, 'PRAGMA journal_mode=WAL;')
        execute_sql(c, 'PRAGMA synchronous=NORMAL;')

        execute_sql(c, '''
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                password TEXT,
                token_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        execute_sql(c, '''
            CREATE TABLE IF NOT EXISTS system_kv (
                `key` TEXT PRIMARY KEY, 
                value TEXT
            )
        ''')
        execute_sql(c, '''
            CREATE TABLE IF NOT EXISTS local_mailboxes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                password TEXT,
                client_id TEXT,
                refresh_token TEXT,
                status INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        execute_sql(c, '''
                    CREATE TABLE IF NOT EXISTS team_accounts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        email TEXT,
                        access_token TEXT,
                        status INTEGER DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        try:
            execute_sql(c, 'ALTER TABLE local_mailboxes ADD COLUMN fission_count INTEGER DEFAULT 0;')
            execute_sql(c, 'ALTER TABLE local_mailboxes ADD COLUMN retry_master INTEGER DEFAULT 0;')
        except Exception:
            pass

        try:
            execute_sql(c, 'ALTER TABLE accounts ADD COLUMN is_active INTEGER DEFAULT 1;')
            execute_sql(c, 'ALTER TABLE accounts ADD COLUMN push_platform VARCHAR(50) DEFAULT NULL;')
            execute_sql(c, 'ALTER TABLE accounts ADD COLUMN push_time VARCHAR(50) DEFAULT NULL;')
        except Exception:
            pass
        try:
            execute_sql(c, 'ALTER TABLE team_accounts ADD COLUMN access_token TEXT;')
        except Exception:
            pass
    print(f"[{cfg.ts()}] [系统] 数据库模块初始化完成 (引擎: {DB_TYPE.upper()})")


def save_account_to_db(email: str, password: str, token_json_str: str) -> bool:
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, '''
                INSERT OR REPLACE INTO accounts (email, password, token_data)
                VALUES (?, ?, ?)
            ''', (email, password, token_json_str))
            return True
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 数据库保存失败: {e}")
        return False


def get_all_accounts() -> list:
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "SELECT email, password, created_at FROM accounts ORDER BY id DESC")
            rows = c.fetchall()
            # MySQL 默认游标返回的也是元组，兼容原版切片逻辑
            return [{"email": r[0], "password": r[1], "created_at": r[2]} for r in rows]
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 获取账号列表失败: {e}")
        return []


def get_token_by_email(email: str) -> dict:
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "SELECT token_data FROM accounts WHERE email = ?", (email,))
            row = c.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return None
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 读取 Token 失败: {e}")
        return None


def get_tokens_by_emails(emails: list) -> list:
    if not emails: return []
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            placeholders = ','.join(['?'] * len(emails))
            execute_sql(c, f"SELECT token_data FROM accounts WHERE email IN ({placeholders})", tuple(emails))
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
    if not emails: return True
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            placeholders = ','.join(['?'] * len(emails))
            execute_sql(c, f"DELETE FROM accounts WHERE email IN ({placeholders})", tuple(emails))
            return True
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 数据库批量删除账号异常: {e}")
        return False


def get_accounts_page(page: int = 1, page_size: int = 50, hide_reg: str = "0", search: str = None, status_filter: str = "all") -> dict:
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            conditions = []
            params = []

            if hide_reg == "1":
                conditions.append("token_data NOT LIKE '%\"仅注册成功\"%'")
            if search:
                conditions.append("(email LIKE ? OR password LIKE ?)")
                search_term = f"%{search}%"
                params.extend([search_term, search_term])

            if status_filter == "active":
                conditions.append("is_active = 1 AND push_platform IS NOT NULL AND push_platform != ''")
            elif status_filter == "disabled":
                conditions.append("is_active = 0 AND push_platform IS NOT NULL AND push_platform != ''")
            elif status_filter == "unpushed":
                conditions.append("(push_platform IS NULL OR push_platform = '')")

            where_clause = ""
            if conditions:
                where_clause = " WHERE " + " AND ".join(conditions)

            count_sql = f"SELECT COUNT(1) FROM accounts{where_clause}"
            if params:
                execute_sql(c, count_sql, tuple(params))
            else:
                execute_sql(c, count_sql)
            total = c.fetchone()[0]

            offset = (page - 1) * page_size
            data_sql = f"SELECT email, password, created_at, token_data, is_active, push_platform, push_time FROM accounts{where_clause} ORDER BY id DESC LIMIT ? OFFSET ?"

            data_params = tuple(params + [page_size, offset])
            execute_sql(c, data_sql, data_params)
            rows = c.fetchall()

            data = [
                {
                    "email": r[0],
                    "password": r[1],
                    "created_at": r[2],
                    "status": "image2api" if '"image2api"' in str(r[3] or "") else (
                        "有凭证" if '"access_token"' in str(r[3] or "") else (
                        "仅注册成功" if '"仅注册成功"' in str(r[3] or "") else "未知")),
                    "is_active": r[4] if r[4] is not None else 1,
                    "push_platform": r[5],
                    "push_time": r[6]
                }
                for r in rows
            ]
            return {"total": total, "data": data}

    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 分页获取账号列表失败: {e}")
        return {"total": 0, "data": []}


def set_sys_kv(key: str, value: Any):
    try:
        val_str = json.dumps(value, ensure_ascii=False)
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "INSERT OR REPLACE INTO system_kv (`key`, value) VALUES (?, ?)", (key, val_str))
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 系统配置保存失败: {e}")


def get_sys_kv(key: str, default=None):
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "SELECT value FROM system_kv WHERE `key` = ?", (key,))
            row = c.fetchone()
            if row:
                return json.loads(row[0])
    except Exception:
        pass
    return default


def get_all_accounts_with_token(limit: int = 10000) -> list:
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "SELECT email, password, token_data FROM accounts ORDER BY id DESC LIMIT ?", (limit,))
            rows = c.fetchall()
            return [{"email": r[0], "password": r[1], "token_data": r[2]} for r in rows]
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 提取完整账号数据失败: {e}")
        return []


def import_local_mailboxes(mailboxes_data: list) -> int:
    count = 0
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            for mb in mailboxes_data:
                try:
                    execute_sql(c, '''
                        INSERT OR IGNORE INTO local_mailboxes (email, password, client_id, refresh_token, status)
                        VALUES (?, ?, ?, ?, 0)
                    ''', (mb['email'], mb['password'], mb.get('client_id', ''), mb.get('refresh_token', '')))
                    if c.rowcount > 0:
                        count += 1
                except:
                    pass
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 导入邮箱库失败: {e}")
    return count


def get_local_mailboxes_page(page: int = 1, page_size: int = 50, search: str = None) -> dict:
    try:
        # as_dict=True 通知游标返回字典格式，适配原来的 sqlite3.Row
        with get_db_conn(as_dict=True) as conn:
            c = get_cursor(conn, as_dict=True)
            conditions = []
            params = []

            if search:
                conditions.append("(email LIKE ?)")
                search_term = f"%{search}%"
                params.extend([search_term])

            where_clause = ""
            if conditions:
                where_clause = " WHERE " + " AND ".join(conditions)
            count_sql = f"SELECT COUNT(1) AS cnt FROM local_mailboxes{where_clause}"
            if params:
                execute_sql(c, count_sql, tuple(params))
            else:
                execute_sql(c, count_sql)

            total_row = c.fetchone()
            total = total_row['cnt'] if DB_TYPE == "mysql" else total_row[0]
            offset = (page - 1) * page_size
            data_sql = f"SELECT * FROM local_mailboxes{where_clause} ORDER BY id DESC LIMIT ? OFFSET ?"
            data_params = tuple(params + [page_size, offset])
            execute_sql(c, data_sql, data_params)
            rows = c.fetchall()

            return {"total": total, "data": [dict(r) for r in rows]}
    except Exception as e:
        print(f"[ERROR] 分页获取邮箱库列表失败: {e}")
        return {"total": 0, "data": []}


def delete_local_mailboxes(ids: list) -> bool:
    if not ids: return True
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            placeholders = ','.join(['?'] * len(ids))
            execute_sql(c, f"DELETE FROM local_mailboxes WHERE id IN ({placeholders})", tuple(ids))
            return True
    except Exception as e:
        return False

def get_and_lock_unused_local_mailbox() -> dict:
    """提取一个未使用的账号，并状态锁定为占用中"""
    try:
        with get_db_conn(as_dict=True) as conn:
            c = get_cursor(conn, as_dict=True)

            filter_sql = """
                            SELECT * FROM local_mailboxes m
                            WHERE status = 0 
                            AND NOT EXISTS (
                                SELECT 1 FROM accounts a WHERE TRIM(LOWER(a.email)) = TRIM(LOWER(m.email))
                            )
                            ORDER BY id ASC LIMIT 1
                        """

            if DB_TYPE == "mysql":
                execute_sql(c, "START TRANSACTION")
                execute_sql(c, filter_sql + " FOR UPDATE")
            else:
                execute_sql(c, "BEGIN EXCLUSIVE")
                execute_sql(c, filter_sql)

            row = c.fetchone()
            if row:
                execute_sql(c, "UPDATE local_mailboxes SET status = 1 WHERE id = ?", (row['id'],))
                return dict(row)
            return None
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 提取本地邮箱失败: {e}")
        return None


def get_mailbox_for_pool_fission() -> dict:
    """带重试优先级的并发取号"""
    try:
        with get_db_conn(as_dict=True) as conn:
            c = get_cursor(conn, as_dict=True)
            if DB_TYPE == "mysql":
                execute_sql(c, "START TRANSACTION")
                execute_sql(c, "SELECT * FROM local_mailboxes WHERE status = 0 AND retry_master = 1 AND email NOT IN (SELECT email FROM accounts) LIMIT 1 FOR UPDATE")
            else:
                execute_sql(c, "BEGIN EXCLUSIVE")
                execute_sql(c, "SELECT * FROM local_mailboxes WHERE status = 0 AND retry_master = 1 AND email NOT IN (SELECT email FROM accounts) LIMIT 1")

            row = c.fetchone()

            if not row:
                if DB_TYPE == "mysql":
                    execute_sql(c, "SELECT * FROM local_mailboxes WHERE status = 0 ORDER BY fission_count ASC LIMIT 1 FOR UPDATE")
                else:
                    execute_sql(c, "SELECT * FROM local_mailboxes WHERE status = 0 ORDER BY fission_count ASC LIMIT 1")
                row = c.fetchone()

            if row:
                execute_sql(c, "UPDATE local_mailboxes SET fission_count = fission_count + 1 WHERE id = ?",
                            (row['id'],))
                return dict(row)
            return None
    except Exception as e:
        print(f"[{cfg.ts()}] [DB_ERROR] 提取失败: {e}")
        return None


def update_local_mailbox_status(email: str, status: int):
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "UPDATE local_mailboxes SET status = ? WHERE email = ?", (status, email))
    except Exception:
        pass

def update_local_mailbox_refresh_token(email: str, new_rt: str):
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "UPDATE local_mailboxes SET refresh_token = ? WHERE email = ?", (new_rt, email))
    except Exception:
        pass


def update_pool_fission_result(email: str, is_blocked: bool, is_raw: bool):
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            if not is_blocked:
                execute_sql(c, "UPDATE local_mailboxes SET retry_master = 0 WHERE email = ?", (email,))
            else:
                if not is_raw:
                    execute_sql(c, "UPDATE local_mailboxes SET retry_master = 1 WHERE email = ?", (email,))
                else:
                    execute_sql(c, "UPDATE local_mailboxes SET status = 3, retry_master = 0 WHERE email = ?", (email,))
    except Exception as e:
        print(f"[{cfg.ts()}] [DB_ERROR] 结果更新失败: {e}")

def clear_retry_master_status(email: str):
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "UPDATE local_mailboxes SET retry_master = 0 WHERE email = ?", (email,))
    except Exception as e:
        print(f"[{cfg.ts()}] [DB_ERROR] 清除 {email} 的 retry_master 状态失败: {e}")

def get_all_accounts_raw() -> list:
    """获取账号库所有原始数据"""
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "SELECT email, password, token_data FROM accounts ORDER BY id DESC")
            rows = c.fetchall()
            return [{"email": r[0], "password": r[1], "token_data": json.loads(r[2]) if r[2] else {}} for r in rows]
    except: return []

def check_account_exists(email: str) -> bool:
    """检查指定邮箱是否已经在本地账号库中"""
    if not email: return False
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "SELECT 1 FROM accounts WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))", (email,))
            return c.fetchone() is not None
    except Exception as e:
        print(f"[{cfg.ts()}] [DB_ERROR] 查重失败: {e}")
        return False

def clear_all_accounts() -> bool:
    """一键清空账号库"""
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "DELETE FROM accounts")
            return True
    except: return False

def get_all_mailboxes_raw() -> list:
    """获取邮箱库所有原始数据"""
    try:
        with get_db_conn(as_dict=True) as conn:
            c = get_cursor(conn, as_dict=True)
            execute_sql(c, "SELECT * FROM local_mailboxes ORDER BY id DESC")
            return [dict(r) for r in c.fetchall()]
    except: return []

def clear_all_mailboxes() -> bool:
    """一键清空邮箱库"""
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "DELETE FROM local_mailboxes")
            return True
    except: return False

def update_account_status(emails: list, is_active: int):
    if not emails: return
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            placeholders = ','.join(['?'] * len(emails))
            execute_sql(c, f"UPDATE accounts SET is_active = ? WHERE email IN ({placeholders})", tuple([is_active] + emails))
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 更新活跃状态失败: {e}")


def update_account_push_info(emails: list, platform: str, mode: str = "overwrite"):
    if not emails: return
    try:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        target_platform = platform.strip().upper()

        with get_db_conn() as conn:
            c = get_cursor(conn)
            for email in emails:
                new_val = target_platform

                if mode == "sync":
                    execute_sql(c, "SELECT push_platform FROM accounts WHERE email = ?", (email,))
                    row = c.fetchone()
                    current_raw = row[0] if row and row[0] else ""

                    if current_raw:
                        parts = [p.strip().upper() for p in current_raw.split(',') if p.strip()]
                        p_set = set(parts)
                        p_set.add(target_platform)
                        new_val = ",".join(sorted(list(p_set)))
                    else:
                        new_val = target_platform

                execute_sql(c, """
                    UPDATE accounts 
                    SET push_platform = ?, push_time = COALESCE(push_time, ?) 
                    WHERE email = ?
                """, (new_val, now_str, email))
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 更新推送状态失败: {e}")

def get_inventory_stats() -> dict:
    try:
        p = "%%" if DB_TYPE == "mysql" else "%"
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, """
                SELECT 
                    COUNT(1) as total,
                    SUM(CASE WHEN (push_platform IS NOT NULL AND push_platform != '') AND is_active = 1 THEN 1 ELSE 0 END) as active_count,
                    SUM(CASE WHEN (push_platform IS NOT NULL AND push_platform != '') AND is_active = 0 THEN 1 ELSE 0 END) as disabled_count,
                    SUM(CASE WHEN push_platform IS NULL OR push_platform = '' THEN 1 ELSE 0 END) as unpushed_count,
                    SUM(CASE WHEN push_platform LIKE '{p}CPA{p}' THEN 1 ELSE 0 END) as cpa_total,
                    SUM(CASE WHEN push_platform LIKE '{p}CPA{p}' AND is_active = 1 THEN 1 ELSE 0 END) as cpa_active,
                    SUM(CASE WHEN push_platform LIKE '{p}CPA{p}' AND is_active = 0 THEN 1 ELSE 0 END) as cpa_disabled,
                    SUM(CASE WHEN push_platform LIKE '{p}SUB2API{p}' THEN 1 ELSE 0 END) as sub_total,
                    SUM(CASE WHEN push_platform LIKE '{p}SUB2API{p}' AND is_active = 1 THEN 1 ELSE 0 END) as sub_active,
                    SUM(CASE WHEN push_platform LIKE '{p}SUB2API{p}' AND is_active = 0 THEN 1 ELSE 0 END) as sub_disabled,
                    SUM(CASE WHEN (push_platform IS NOT NULL AND push_platform != '') THEN 1 ELSE 0 END) as cloud_total
                FROM accounts
            """)
            row = c.fetchone()
            r = [x or 0 for x in row] if row else [0] * 11

            return {
                "local": {
                    "total": r[0],
                    "active": r[1],
                    "disabled": r[2],
                    "unpushed": r[3]
                },
                "cloud": {
                    "total": r[10],
                    "enabled": r[1],
                    "cpa": r[4],
                    "cpa_active": r[5],
                    "cpa_disabled": r[6],
                    "sub2api": r[7],
                    "sub2api_active": r[8],
                    "sub2api_disabled": r[9]
                }
            }
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 获取统计数据失败: {e}")
        return {
            "local": {"total": 0, "active": 0, "disabled": 0, "unpushed": 0},
            "cloud": {"total": 0, "enabled": 0, "cpa": 0, "cpa_active": 0, "cpa_disabled": 0, "sub2api": 0,
                      "sub2api_active": 0, "sub2api_disabled": 0}
        }

def update_account_status_by_truncated_name(truncated_name: str, is_active: int):
    if not truncated_name or truncated_name == "unknown": return
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "UPDATE accounts SET is_active = ? WHERE SUBSTR(email, 1, 64) = ?", (is_active, truncated_name))
    except Exception as e:
        print(f"[ERROR] 按截断名称更新活跃状态失败: {e}")

def remove_account_push_platform(identifier: str, platform: str, exact_match: bool = True):
    if not identifier: return
    target_platform = platform.strip().upper()
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            if exact_match:
                execute_sql(c, "SELECT email, push_platform FROM accounts WHERE email = ?", (identifier,))
            else:
                execute_sql(c, "SELECT email, push_platform FROM accounts WHERE SUBSTR(email, 1, 64) = ?",
                            (identifier,))

            rows = c.fetchall()
            for row in rows:
                db_email = row[0]
                current_raw = row[1] if row[1] else ""

                parts = [p.strip().upper() for p in current_raw.split(',') if p.strip()]
                if target_platform in parts:
                    parts.remove(target_platform)
                    new_val = ",".join(sorted(list(set(parts)))) if parts else ""
                    execute_sql(c, "UPDATE accounts SET push_platform = ?, is_active = 0 WHERE email = ?",
                                (new_val, db_email))
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 剥离推送平台记录失败: {e}")

def get_account_full_info(email: str) -> dict:
    try:
        with get_db_conn(as_dict=True) as conn:
            c = get_cursor(conn, as_dict=True)
            execute_sql(c, "SELECT password, token_data, push_platform FROM accounts WHERE email = ?", (email,))
            row = c.fetchone()
            if row:
                res = dict(row)
                res['token_data'] = json.loads(res['token_data']) if res['token_data'] else {}
                return res
            return None
    except Exception as e:
        print(f"[ERROR] 获取账号全量信息失败: {e}")
        return None

def update_account_token_only(email: str, token_json_str: str) -> bool:
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "UPDATE accounts SET token_data = ? WHERE email = ?", (token_json_str, email))
            return True
    except Exception as e:
        print(f"[ERROR] 仅更新 Token 失败: {e}")
        return False

def import_team_accounts(team_data_list: list) -> int:
    count = 0
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            for td in team_data_list:
                try:
                    execute_sql(c, '''
                        INSERT OR IGNORE INTO team_accounts (email, access_token, status)
                        VALUES (?, ?, ?)
                    ''', (td['email'], td['access_token'], td.get('status', 1)))
                    if c.rowcount > 0:
                        count += 1
                except Exception as ex:
                    pass
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 导入 Team 库失败: {e}")
    return count


def get_team_accounts_page(page: int = 1, page_size: int = 50, search: str = None) -> dict:
    try:
        with get_db_conn(as_dict=True) as conn:
            c = get_cursor(conn, as_dict=True)
            conditions = []
            params = []

            if search:
                conditions.append("(email LIKE ? OR access_token LIKE ?)")
                search_term = f"%{search}%"
                params.extend([search_term, search_term])

            where_clause = ""
            if conditions:
                where_clause = " WHERE " + " AND ".join(conditions)

            count_sql = f"SELECT COUNT(1) AS cnt FROM team_accounts{where_clause}"
            if params:
                execute_sql(c, count_sql, tuple(params))
            else:
                execute_sql(c, count_sql)

            total_row = c.fetchone()
            total = total_row['cnt'] if DB_TYPE == "mysql" else total_row[0]
            offset = (page - 1) * page_size

            data_sql = f"SELECT id, email, access_token, status, created_at FROM team_accounts{where_clause} ORDER BY id DESC LIMIT ? OFFSET ?"
            data_params = tuple(params + [page_size, offset])
            execute_sql(c, data_sql, data_params)
            rows = c.fetchall()

            return {"total": total, "data": [dict(r) for r in rows]}
    except Exception as e:
        print(f"[ERROR] 分页获取 Team 库列表失败: {e}")
        return {"total": 0, "data": []}


def delete_team_accounts(ids: list) -> bool:
    if not ids: return True
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            placeholders = ','.join(['?'] * len(ids))
            execute_sql(c, f"DELETE FROM team_accounts WHERE id IN ({placeholders})", tuple(ids))
            return True
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 删除 Team 账号失败: {e}")
        return False


def clear_all_team_accounts() -> bool:
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "DELETE FROM team_accounts")
            return True
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 清空 Team 库失败: {e}")
        return False


def get_random_team_account() -> dict:
    try:
        with _team_db_lock:
            with get_db_conn(as_dict=True) as conn:
                c = get_cursor(conn, as_dict=True)
                order_clause = "RAND()" if DB_TYPE == "mysql" else "RANDOM()"
                sql = f"SELECT id, email, access_token FROM team_accounts WHERE status = 1 ORDER BY {order_clause} LIMIT 1"
                execute_sql(c, sql)
                row = c.fetchone()
                if row:
                    return dict(row)
                return None
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 随机提取 Team 账号失败: {e}")
        return None

def get_all_team_accounts() -> list:
    try:
        with get_db_conn(as_dict=True) as conn:
            c = get_cursor(conn, as_dict=True)
            execute_sql(c, "SELECT id, email, access_token FROM team_accounts WHERE status = 1")
            rows = c.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 获取所有 Team 账号失败: {e}")
        return []

def delete_sys_kvs(keys: list) -> bool:
    if not keys: return True
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            placeholders = ','.join(['?'] * len(keys))
            execute_sql(c, f"DELETE FROM system_kv WHERE `key` IN ({placeholders})", tuple(keys))
            return True
    except Exception as e:
        print(f"[{cfg.ts()}] [ERROR] 批量删除系统 KV 异常: {e}")
        return False