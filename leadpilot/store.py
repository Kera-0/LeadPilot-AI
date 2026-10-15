import json
import sqlite3
import threading
import time


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, path='leads.db'):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS clients (
          id TEXT PRIMARY KEY, channel TEXT, external_id TEXT, state TEXT,
          created REAL, updated REAL);
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY, client TEXT REFERENCES clients(id) ON DELETE CASCADE,
          direction TEXT, text TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS requests (
          client TEXT REFERENCES clients(id) ON DELETE CASCADE,
          id TEXT, response TEXT, PRIMARY KEY(client,id));
        CREATE TABLE IF NOT EXISTS notifications (
          id INTEGER PRIMARY KEY, client TEXT REFERENCES clients(id) ON DELETE CASCADE,
          text TEXT, attempts INTEGER DEFAULT 0, available REAL DEFAULT 0, delivered REAL);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, client TEXT REFERENCES clients(id) ON DELETE CASCADE,
          name TEXT, data TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        ''')
        self.db.commit()

    def client(self, cid):
        row = self.db.execute('SELECT * FROM clients WHERE id=?', (cid,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['state'] = json.loads(row['state'])
        return result

    def save(self, cid, channel, external_id, state):
        now = time.time()
        self.db.execute('''INSERT INTO clients VALUES(?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET state=excluded.state,updated=excluded.updated''',
                        (cid, channel, external_id, dumps(state), now, now))

    def event(self, cid, name, data=None):
        self.db.execute('INSERT INTO events(client,name,data,created) VALUES(?,?,?,?)',
                        (cid, name, dumps(data or {}), time.time()))

    def history(self, cid):
        return [dict(r) for r in self.db.execute('SELECT direction,text,created FROM messages WHERE client=? ORDER BY id', (cid,))]

    def metrics(self):
        rows = list(self.db.execute('SELECT client,name,data FROM events'))
        groups = {}
        for row in rows:
            groups.setdefault(row['name'], set()).add(row['client'])
        count = lambda key: len(groups.get(key, set()))
        dialogs, leads = count('consent'), count('qualified')
        times = [json.loads(r['data'])['seconds'] for r in rows if r['name'] == 'response']
        reasons = {}
        for row in rows:
            if row['name'] == 'objection':
                reason = json.loads(row['data'])['reason']
                reasons[reason] = reasons.get(reason, 0) + 1
        return {'dialogs': dialogs, 'qualified': leads,
                'dialog_to_lead': leads / dialogs if dialogs else 0,
                'handoff_share': count('handoff') / dialogs if dialogs else 0,
                'response_avg_seconds': sum(times) / len(times) if times else None,
                'response_under_30_share': sum(t < 30 for t in times) / len(times) if times else None,
                'objections': reasons,
                'notifications_pending': self.db.execute('SELECT count(*) FROM notifications WHERE delivered IS NULL').fetchone()[0]}
