"""Persistência em SQLite."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from .config import DB_PATH, ensure_dirs

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    preset TEXT DEFAULT 'VSL',
    status TEXT DEFAULT 'novo',
    info_json TEXT DEFAULT '{}',
    plan_json TEXT DEFAULT '{}',
    analysis_json TEXT DEFAULT '{}',
    created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT,
    info_json TEXT DEFAULT '{}',
    -- o que É este arquivo e como o usuário quer usá-lo. É isto que a IA lê
    -- para posicionar: um quadro de 360 px mostra o que a imagem mostra, não
    -- a intenção de quem gravou.
    descricao TEXT DEFAULT '',
    created_at REAL
);
CREATE TABLE IF NOT EXISTS presets (
    name TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    builtin INTEGER DEFAULT 0,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    replacement TEXT NOT NULL,
    enabled INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    kind TEXT,
    status TEXT,
    progress REAL DEFAULT 0,
    stage TEXT DEFAULT '',
    message TEXT DEFAULT '',
    result_json TEXT DEFAULT '{}',
    error TEXT DEFAULT '',
    created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE INDEX IF NOT EXISTS idx_media_project ON media(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
"""


def connect() -> sqlite3.Connection:
    global _initialized
    conn = getattr(_local, "conn", None)
    if conn is None:
        ensure_dirs()
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    if not _initialized:
        with _init_lock:
            if not _initialized:
                conn.executescript(SCHEMA)
                _migrar(conn)
                conn.commit()
                _seed(conn)
                _initialized = True
    return conn


def _migrar(conn: sqlite3.Connection) -> None:
    """Colunas novas em bancos que já existem.

    CREATE TABLE IF NOT EXISTS não acrescenta coluna nenhuma a uma tabela que
    já está lá: quem já usava o editor ficaria sem o campo e a rota quebraria
    na primeira escrita.
    """
    novas = {
        "media": [("descricao", "TEXT DEFAULT ''")],
    }
    for tabela, colunas in novas.items():
        try:
            existentes = {r["name"] for r in
                          conn.execute(f"PRAGMA table_info({tabela})")}
        except sqlite3.Error:
            continue
        for nome, tipo in colunas:
            if nome not in existentes:
                conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")


def _seed(conn: sqlite3.Connection) -> None:
    from .presets import BUILTIN, PRESETS_VERSION
    from .subtitles.corrections import DEFAULTS

    # INSERT OR IGNORE sozinho congelava os presets na primeira execução: toda
    # melhoria posterior (tamanho de fonte, parâmetros de zoom) ficava presa no
    # código e nunca chegava a quem já tinha o app instalado. A versão destrava.
    row = conn.execute("SELECT value FROM settings WHERE key='presets_version'").fetchone()
    try:
        atual = int(json.loads(row["value"])) if row else 0
    except (TypeError, ValueError):
        atual = 0
    atualizar = atual < PRESETS_VERSION

    for preset in BUILTIN:
        if atualizar:
            # só os EMBUTIDOS são refeitos; preset que o usuário salvou com
            # nome próprio (builtin=0) nunca é tocado
            conn.execute(
                "INSERT INTO presets(name, data_json, builtin, updated_at) "
                "VALUES (?,?,1,?) ON CONFLICT(name) DO UPDATE SET "
                "data_json=excluded.data_json, updated_at=excluded.updated_at "
                "WHERE presets.builtin=1",
                (preset["name"], json.dumps(preset), time.time()),
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO presets(name, data_json, builtin, updated_at) "
                "VALUES (?,?,1,?)",
                (preset["name"], json.dumps(preset), time.time()),
            )
    if atualizar:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('presets_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(PRESETS_VERSION),),
        )
    n = conn.execute("SELECT COUNT(*) c FROM corrections").fetchone()["c"]
    if n == 0:
        for d in DEFAULTS:
            conn.execute(
                "INSERT INTO corrections(pattern, replacement, enabled) VALUES (?,?,1)",
                (d["from"], d["to"]),
            )
    conn.commit()


def q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, args).fetchall()


def q1(sql: str, args: tuple = ()) -> sqlite3.Row | None:
    return connect().execute(sql, args).fetchone()


def ex(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    conn = connect()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur


def jloads(value: Any, default: Any = None) -> Any:
    if not value:
        return default if default is not None else {}
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def jdumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ------------------------------------------------------------------ correções
def list_corrections() -> list[dict]:
    return [{"id": r["id"], "from": r["pattern"], "to": r["replacement"],
             "enabled": bool(r["enabled"])}
            for r in q("SELECT * FROM corrections ORDER BY id")]


def add_correction(pattern: str, replacement: str) -> dict:
    cur = ex("INSERT INTO corrections(pattern, replacement, enabled) VALUES (?,?,1)",
             (pattern, replacement))
    return {"id": cur.lastrowid, "from": pattern, "to": replacement, "enabled": True}


def update_correction(cid: int, pattern: str, replacement: str, enabled: bool) -> None:
    ex("UPDATE corrections SET pattern=?, replacement=?, enabled=? WHERE id=?",
       (pattern, replacement, 1 if enabled else 0, cid))


def delete_correction(cid: int) -> None:
    ex("DELETE FROM corrections WHERE id=?", (cid,))


# -------------------------------------------------------------------- settings
def get_setting(key: str, default: Any = None) -> Any:
    row = q1("SELECT value FROM settings WHERE key=?", (key,))
    return jloads(row["value"], default) if row else default


def set_setting(key: str, value: Any) -> None:
    ex("INSERT INTO settings(key, value) VALUES (?,?) "
       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, jdumps(value)))
