import json
import re
import unicodedata
from pathlib import Path

from mrac_contracts.execution import ContractError, atomic, database, digest, now, parse_yaml

from .home import checked, copy_package, home, inventory
from .locks import file_lock


def normalized(name):
    if (
        not isinstance(name, str)
        or not name
        or name.strip() != name
        or any(unicodedata.category(c) == "Cc" or c in "/\\@" for c in name)
        or re.fullmatch(r"(?:[Cc])?\d+", name)
    ):
        raise ContractError("Case name is empty, reserved or contains invalid characters")
    return unicodedata.normalize("NFC", name).casefold()


def validate_package(path):
    path = Path(path)
    files = inventory(path)
    # Import-free validation shared with registration. Runner performs its full schema validation.
    data = parse_yaml((path / "case.yaml").read_bytes())
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("id"), str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", data["id"])
    ):
        raise ContractError("Invalid case id")
    if type(data.get("version")) is not int or data["version"] < 1:
        raise ContractError("Case version must be a positive integer")
    repo = data.get("repository", {})
    if not repo.get("url") or not re.fullmatch(
        r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", repo.get("commit", "")
    ):
        raise ContractError("Case requires repository URL and full commit")
    if data.get("track", {}).get("type") != "spec":
        raise ContractError("Unsupported case track")
    if not isinstance(repo["url"], str) or repo["url"].startswith("-"):
        raise ContractError("Invalid repository URL")
    limits = data.get("limits", {})
    if not isinstance(limits, dict):
        raise ContractError("limits must be a mapping")
    for key in ("max_audit_rounds", "agent_timeout_seconds"):
        if key in limits and (type(limits[key]) is not int or limits[key] <= 0):
            raise ContractError("Case limits must be positive integers")
    seen = {"case.yaml"}
    related = data.get("related_specs", [])
    if not isinstance(related, list) or any(
        not isinstance(item, dict) or set(item) != {"file", "sha256"} for item in related
    ):
        raise ContractError("Related Specs require only file and sha256")
    for entry in [data.get("task", {}), *related]:
        name = entry.get("file", "")
        if name in seen:
            raise ContractError("Duplicate case input")
        seen.add(name)
        checked(path, path / name)
        if name not in files or files[name] != str(entry.get("sha256", "")).lower():
            raise ContractError(f"Invalid case input/hash: {name}")
        if not (path / name).read_text(encoding="utf-8-sig").strip():
            raise ContractError("Case input must be nonempty UTF-8")
    return data, files


class Registry:
    def __init__(self, root=None):
        self.root = home(root) / "cases"
        self.db = database(self.root / "registry.sqlite")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                number INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                source_id TEXT NOT NULL, default_version INTEGER, archived INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS names (name TEXT PRIMARY KEY, number INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS versions (
                number INTEGER, version INTEGER, manifest TEXT NOT NULL,
                PRIMARY KEY(number,version));
            CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, request TEXT NOT NULL, number INTEGER NOT NULL,
                version INTEGER NOT NULL, ready INTEGER DEFAULT 0);
        """)

    def close(self):
        self.db.close()

    def _number(self, selector):
        text = str(selector)
        if re.fullmatch(r"[Cc]?\d+", text):
            return int(text.lstrip("Cc"))
        row = self.db.execute(
            "SELECT number FROM names WHERE name=?", (normalized(text),)
        ).fetchone()
        if not row:
            raise ContractError(f"Unknown case: {selector}")
        return row[0]

    def resolve(self, selector, version=None, *, archived=False):
        number = self._number(selector)
        row = self.db.execute("SELECT * FROM cases WHERE number=?", (number,)).fetchone()
        if not row or row["default_version"] is None or (row["archived"] and not archived):
            raise ContractError("Case is unavailable or archived")
        version = row["default_version"] if version is None else version
        item = self.db.execute(
            "SELECT manifest FROM versions WHERE number=? AND version=?", (number, version)
        ).fetchone()
        if not item:
            raise ContractError("Unknown case version")
        path = checked(
            self.root, self.root / "packages" / f"C{number:06d}" / f"v{version}" / "package"
        )
        manifest = json.loads(item[0])
        if inventory(path) != manifest:
            raise ContractError("Registered case bytes changed")
        return {
            **dict(row),
            "case_key": f"C{number:06d}",
            "version": version,
            "path": str(path),
            "manifest": manifest,
            "sha256": digest(manifest),
        }

    def register(self, source, request_id, *, name=None, selector=None):
        source = Path(source).resolve()
        data, files = validate_package(source)
        request = digest(
            {"source": str(source), "name": name, "selector": selector, "manifest": files}
        )
        with file_lock(self.root / "registry.lock"):
            old = self.db.execute("SELECT * FROM operations WHERE id=?", (request_id,)).fetchone()
            if old and old["request"] != request:
                raise ContractError("Registration request ID reused with different content")
            if old and old["ready"]:
                return self.resolve(old["number"], old["version"], archived=True)
            if old:
                number = old["number"]
            else:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    if selector is None:
                        chosen = data["id"] if name is None else name
                        key = normalized(chosen)
                        cursor = self.db.execute(
                            "INSERT INTO cases(name,source_id) VALUES(?,?)", (chosen, data["id"])
                        )
                        number = cursor.lastrowid
                        self.db.execute("INSERT INTO names VALUES(?,?)", (key, number))
                    else:
                        existing = self.resolve(selector, archived=True)
                        number = existing["number"]
                        if data["id"] != existing["source_id"]:
                            raise ContractError("source_id cannot change")
                    previous = self.db.execute(
                        "SELECT manifest FROM versions WHERE number=? AND version=?",
                        (number, data["version"]),
                    ).fetchone()
                    if previous and json.loads(previous[0]) != files:
                        raise ContractError("Cannot overwrite a registered version")
                    highest = self.db.execute(
                        "SELECT max(version) FROM versions WHERE number=?", (number,)
                    ).fetchone()[0]
                    if not previous and highest and data["version"] <= highest:
                        raise ContractError("New version must exceed highest registered version")
                    self.db.execute(
                        "INSERT INTO operations VALUES(?,?,?,?,0)",
                        (request_id, request, number, data["version"]),
                    )
                    self.db.execute("COMMIT")
                except BaseException:
                    self.db.execute("ROLLBACK")
                    raise
            target = self.root / "packages" / f"C{number:06d}" / f"v{data['version']}"
            checked(self.root, target)
            if not target.exists():
                staging = self.root / ".staging" / digest(request_id)
                copy_package(source, staging / "package")
                atomic(
                    staging / "manifest.json",
                    {"files": files, "sha256": digest(files), "at": now()},
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                staging.rename(target)
            if inventory(target / "package") != files:
                raise ContractError("Uncommitted package differs from registration")
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(
                    "INSERT OR IGNORE INTO versions VALUES(?,?,?)",
                    (number, data["version"], json.dumps(files)),
                )
                self.db.execute(
                    "UPDATE cases SET default_version=max(coalesce(default_version,0),?) WHERE number=?",
                    (data["version"], number),
                )
                self.db.execute("UPDATE operations SET ready=1 WHERE id=?", (request_id,))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            return self.resolve(number, data["version"], archived=True)

    def list(self):
        return [
            dict(row) | {"case_key": f"C{row['number']:06d}"}
            for row in self.db.execute(
                "SELECT * FROM cases WHERE default_version IS NOT NULL ORDER BY number"
            )
        ]

    def update(self, selector, *, name=None, archived=None):
        with file_lock(self.root / "registry.lock"):
            number = self._number(selector)
            self.resolve(number, archived=True)
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if name is not None:
                    key = normalized(name)
                    existing = self.db.execute(
                        "SELECT number FROM names WHERE name=?", (key,)
                    ).fetchone()
                    if existing and existing[0] != number:
                        raise ContractError("Name or alias already belongs to another case")
                    self.db.execute("INSERT OR IGNORE INTO names VALUES(?,?)", (key, number))
                    self.db.execute("UPDATE cases SET name=? WHERE number=?", (name, number))
                if archived is not None:
                    self.db.execute(
                        "UPDATE cases SET archived=? WHERE number=?", (int(archived), number)
                    )
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            return self.resolve(number, archived=True)
