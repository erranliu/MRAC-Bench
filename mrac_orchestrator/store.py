import json
from contextlib import contextmanager

from mrac_contracts.execution import ContractError, database, digest, now
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

    def batch_request(self, request_id):
        row = self.db.execute(
            "SELECT id,request_hash FROM batches WHERE request_id=?", (request_id,)
        ).fetchone()
        return dict(row) if row else None

    def publish_batch(self, plan):
        """Publish the frozen batch, tasks and submit event in one transaction."""
        with self.transaction():
            self.db.execute(
                "INSERT INTO batches VALUES(?,?,?,?)",
                (plan["id"], plan["request_id"], plan["request_hash"], json.dumps(plan)),
            )
            for task in plan["tasks"]:
                self.db.execute(
                    "INSERT INTO tasks VALUES(?,?,?,?,?)",
                    (task["id"], plan["id"], "QUEUED", 0, json.dumps(task)),
                )
            self.event(
                plan["id"],
                "submitted",
                {
                    "request_id": plan["request_id"],
                    "task_count": len(plan["tasks"]),
                },
            )

    @staticmethod
    def _task(row):
        return json.loads(row["data"]) | {"state": row["state"], "revision": row["revision"]}

    def tasks(self, batch_id=None):
        query, parameters = "SELECT * FROM tasks", ()
        if batch_id:
            query += " WHERE batch_id=?"
            parameters = (batch_id,)
        return [self._task(row) for row in self.db.execute(query + " ORDER BY rowid", parameters)]

    def task(self, batch_id, task_id):
        row = self.db.execute(
            "SELECT * FROM tasks WHERE batch_id=? AND id=?", (batch_id, task_id)
        ).fetchone()
        return self._task(row) if row else None

    def _require_transaction(self):
        if not self.db.in_transaction:
            raise ContractError("This write requires a Store transaction")

    def claim(self, task, attempt):
        """Claim inside the scheduler's capacity/dependency transaction."""
        self._require_transaction()
        self.db.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?)",
            (attempt["id"], task["batch_id"], task["id"], "STARTING", json.dumps(attempt)),
        )
        return self.update(task, "STARTING", attempt_id=attempt["id"])

    def finish_attempt(self, task, attempt_id, state, **changes):
        self._require_transaction()
        updated = self.update(task, state, **changes)
        self.db.execute("UPDATE attempts SET state=? WHERE id=?", (state, attempt_id))
        return updated

    def operation_result(self, operation_id, request_hash):
        row = self.db.execute(
            "SELECT request_hash,result FROM operations WHERE id=?", (operation_id,)
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ContractError("Operation ID reused with different request")
        return json.loads(row["result"])

    def record_operation(self, operation_id, request_hash, result):
        self._require_transaction()
        self.db.execute(
            "INSERT INTO operations VALUES(?,?,?)",
            (operation_id, request_hash, json.dumps(result)),
        )

    def import_resource_event(self, batch_id, event):
        """Deduplicate resource events in the caller's export transaction."""
        self._require_transaction()
        operation_id = f"{event['id']}-{batch_id}"
        if self.db.execute("SELECT 1 FROM operations WHERE id=?", (operation_id,)).fetchone():
            return False
        self.event(batch_id, event["kind"], event)
        self.record_operation(operation_id, digest(event), {"imported": True})
        return True

    def events(self, batch_id):
        return [
            {
                "event_id": f"{batch_id}:{row['seq']}",
                "seq": row["seq"],
                "at": row["at"],
                **json.loads(row["data"]),
            }
            for row in self.db.execute(
                "SELECT * FROM events WHERE batch_id=? ORDER BY seq", (batch_id,)
            )
        ]

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
