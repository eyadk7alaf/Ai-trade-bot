import sqlite3, time
DB_PATH = 'bot_data.db'
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER UNIQUE, username TEXT, active INTEGER DEFAULT 0, expiry INTEGER DEFAULT 0)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS keys (id INTEGER PRIMARY KEY AUTOINCREMENT, key_code TEXT UNIQUE, duration_days INTEGER, used_by INTEGER, created_at INTEGER, expiry INTEGER)''')
    conn.commit(); conn.close()
def add_or_update_user(telegram_id, username=None):
    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE telegram_id=?', (telegram_id,))
    row = cur.fetchone()
    if row: cur.execute('UPDATE users SET username=? WHERE telegram_id=?', (username, telegram_id))
    else: cur.execute('INSERT INTO users (telegram_id, username) VALUES (?,?)', (telegram_id, username))
    conn.commit(); conn.close()
def activate_user_with_key(telegram_id, key_code):
    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT * FROM keys WHERE key_code=?', (key_code,))
    k = cur.fetchone()
    if not k: conn.close(); return False,'invalid'
    if k['used_by'] is not None: conn.close(); return False,'used'
    now = int(time.time())
    duration = k['duration_days']
    expiry = now + duration*24*3600
    cur.execute('UPDATE keys SET used_by=?, expiry=? WHERE key_code=?', (telegram_id, expiry, key_code))
    cur.execute('UPDATE users SET active=1, expiry=? WHERE telegram_id=?', (expiry, telegram_id))
    conn.commit(); conn.close()
    return True, expiry
def get_active_users():
    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT telegram_id, username, expiry FROM users WHERE active=1')
    rows = cur.fetchall(); conn.close()
    return rows
def create_key(key_code, duration_days):
    conn = get_conn(); cur = conn.cursor()
    now = int(time.time())
    cur.execute('INSERT INTO keys (key_code, duration_days, created_at) VALUES (?,?,?)', (key_code, duration_days, now))
    conn.commit(); conn.close()
def list_keys():
    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT * FROM keys ORDER BY id DESC')
    rows = cur.fetchall(); conn.close()
    return rows