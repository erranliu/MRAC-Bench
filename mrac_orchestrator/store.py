import json
from contextlib import contextmanager

from mrac_contracts.execution import ContractError, database, now
from mrac_resources.home import home


class Store:
    def __init__(self, root=None):
        self.home = home(root)
        self.db = database(self.home / "control/scheduler.sqlite")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, request_id TEXT UNIQUE,
                request_hash TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT, batch_id TEXT REFERENCES batches(id),
                state TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(batch_id,id));
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, batch_id TEXT, task_id TEXT,
                state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(batch_id TEXT, seq INTEGER, at TEXT, data TEXT,
                PRIMARY KEY(batch_id,seq));
        """)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def event(self, batch, kind, data):
        seq = self.db.execute(
            "SELECT coalesce(max(seq),0)+1 FROM events WHERE batch_id=?", (batch,)
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO events VALUES(?,?,?,?)",
            (batch, seq, now(), json.dumps({"kind": kind, **data})),
        )

    def batch(self, batch_id):
        row = self.db.execute("SELECT data FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not row:
            raise ContractError("Unknown batch")
        return json.loads(row[0])

    def batches(self):
        return [
            json.loads(row[0]) for row in self.db.execute("SELECT data FROM batches ORDER BY rowid")
        ]

    def tasks(self, batch_id=None):
        query, parameters = "SELECT * FROM tasks", ()
        if batch_id:
            query += " WHERE batch_id=?"
            parameters = (batch_id,)
        return [
            json.loads(row["data"]) | {"state": row["state"], "revision": row["revision"]}
            for row in self.db.execute(query + " ORDER BY rowid", parameters)
        ]

    def task(self, batch_id, task_id):
        return next((t for t in self.tasks(batch_id) if t["id"] == task_id), None)

    def update(self, task, state, **changes):
        updated = {**task, **changes, "state": state, "revision": task["revision"] + 1}
        cursor = self.db.execute(
            "UPDATE tasks SET state=?,revision=?,data=? WHERE batch_id=? AND id=? AND revision=?",
            (
                state,
                updated["revision"],
                json.dumps(updated),
                task["batch_id"],
                task["id"],
                task["revision"],
            ),
        )
        if cursor.rowcount != 1:
            raise ContractError("Task revision conflict")
        self.event(
            task["batch_id"],
            "task_state",
            {
                "task_id": task["id"],
                "from": task["state"],
                "to": state,
                "revision": updated["revision"],
            },
        )
        return updated

    def attempts(self, batch=None):
        query = "SELECT data,state FROM attempts"
        args = ()
        if batch:
            query += " WHERE batch_id=?"
            args = (batch,)
        return [json.loads(row[0]) | {"state": row[1]} for row in self.db.execute(query, args)]

    def close(self):
        self.db.close()
