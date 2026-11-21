"""bridge.py — 桥检夜班原页核对。

门户(Portal)按栏目定版、按编号升序分页;站点(Station)冻结任务口径,
拉页同存原页与续点,仅凭登记、交件与预配 key 验实发同版首尾链后入账。
原读乘正有理 s 加整数 z 得精确微米值,严格超有理 h 计超限;
断源重开后仍可凭批号领回报文(冻结口径、原页、行位、换算、编号序超限项及精确比例)。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
from fractions import Fraction

__all__ = ["Portal", "Station", "Reject"]


class Reject(ValueError):
    """业务拒绝:错版游标、缺尾、串版、跨路径拼页、伪页、未知引用、同号异参等。"""


# ---------------------------------------------------------------- 基础工具

def _canon(obj) -> str:
    """确定性 JSON 串行化,用于登记比对与签名。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _key_bytes(key) -> bytes:
    if isinstance(key, bytes):
        return key
    if isinstance(key, str):
        return key.encode("utf-8")
    raise TypeError("key 须为 str 或 bytes")


def _sig(key: bytes, msg: str) -> str:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _frac(x, name) -> Fraction:
    """有理数入参:Fraction / int / 'a/b' 字符串,拒绝浮点以免失精度。"""
    if isinstance(x, Fraction):
        return x
    if _is_int(x):
        return Fraction(x)
    if isinstance(x, str):
        try:
            return Fraction(x)
        except (ValueError, ZeroDivisionError):
            pass
    raise Reject(f"{name} 须为有理数(Fraction/int/'a/b'),得 {x!r}")


def _connect(db) -> sqlite3.Connection:
    if isinstance(db, sqlite3.Connection):
        return db
    if isinstance(db, (str, bytes)) or hasattr(db, "__fspath__"):
        return sqlite3.connect(os.fspath(db))
    raise TypeError("db 须为 sqlite3.Connection 或数据库路径")


def _norm_row(row):
    """行:[测点ID, 整数编号, 整数原读, 源行版]。"""
    if not isinstance(row, (list, tuple)) or len(row) != 4:
        raise Reject(f"行须为[测点ID,整数编号,整数原读,源行版]: {row!r}")
    pid, num, reading, rowver = row
    if isinstance(pid, bool) or not isinstance(pid, (str, int)):
        raise Reject(f"测点ID须为字符串或整数: {pid!r}")
    if not _is_int(num):
        raise Reject(f"编号须为整数: {num!r}")
    if not _is_int(reading):
        raise Reject(f"原读须为整数: {reading!r}")
    if not _is_int(rowver):
        raise Reject(f"源行版须为整数: {rowver!r}")
    return [pid, num, reading, rowver]


# ---------------------------------------------------------------- 门户

class Portal:
    """栏目定版门户:publish 固定栏目版,page 按编号升序出页。"""

    def __init__(self, db, key):
        self._key = _key_bytes(key)
        self._conn = _connect(db)
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portal_versions(
                    c        TEXT NOT NULL,
                    v        INTEGER NOT NULL,
                    rows_json TEXT NOT NULL,
                    PRIMARY KEY(c, v))
                """
            )

    def close(self):
        self._conn.close()

    def publish(self, c, v, rows):
        """固定栏目 c 的版 v;版内 ID 与编号各唯一;同号异参拒绝,同件复登免改。"""
        if not isinstance(c, str) or not c:
            raise Reject("栏目c须为非空字符串")
        if not _is_int(v):
            raise Reject("版v须为整数")
        if not isinstance(rows, (list, tuple)):
            raise Reject("rows 须为行的列表")
        norm = [_norm_row(r) for r in rows]
        ids = [r[0] for r in norm]
        nums = [r[1] for r in norm]
        if len(set(ids)) != len(ids):
            raise Reject("版内测点ID重复")
        if len(set(nums)) != len(nums):
            raise Reject("版内编号重复")
        norm.sort(key=lambda r: r[1])  # 按编号升序固定
        blob = _canon(norm)
        with self._conn:
            got = self._conn.execute(
                "SELECT rows_json FROM portal_versions WHERE c=? AND v=?", (c, v)
            ).fetchone()
            if got is not None:
                if got[0] != blob:
                    raise Reject(f"同号异参:栏目{c}版{v}已固定,拒绝改版")
                return
            self._conn.execute(
                "INSERT INTO portal_versions(c,v,rows_json) VALUES(?,?,?)", (c, v, blob)
            )

    def has_version(self, c, v) -> bool:
        got = self._conn.execute(
            "SELECT 1 FROM portal_versions WHERE c=? AND v=?", (c, v)
        ).fetchone()
        return got is not None

    def page(self, c, v, k, n):
        """按编号升序出页;k=None 起读,正页长 n 可换;末页 next=null;拒错版游标。"""
        if not _is_int(n) or n < 1:
            raise Reject("页长n须为正整数")
        got = self._conn.execute(
            "SELECT rows_json FROM portal_versions WHERE c=? AND v=?", (c, v)
        ).fetchone()
        if got is None:
            raise Reject(f"未知引用:栏目{c}版{v}未发布")
        rows = json.loads(got[0])
        after = None if k is None else self._decode_cursor(k, c, v)
        if after is not None:
            rows = [r for r in rows if r[1] > after]
        chunk = rows[: n + 1]
        page_rows = chunk[:n]
        nxt = self._encode_cursor(c, v, page_rows[-1][1]) if len(chunk) > n else None
        item = {"c": c, "v": v, "rows": page_rows, "next": nxt}
        item["sig"] = _sig(self._key, _canon(item))
        return item

    def _encode_cursor(self, c, v, after) -> str:
        body = base64.urlsafe_b64encode(
            _canon({"c": c, "v": v, "after": after}).encode("utf-8")
        ).decode("ascii")
        return body + "." + _sig(self._key, body)

    def _decode_cursor(self, k, c, v):
        if not isinstance(k, str) or "." not in k:
            raise Reject("游标畸形")
        body, sig = k.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sig(self._key, body)):
            raise Reject("游标伪造")
        try:
            payload = json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
        except Exception:
            raise Reject("游标畸形")
        if not isinstance(payload, dict) or payload.get("c") != c or payload.get("v") != v:
            raise Reject("错版游标")
        after = payload.get("after")
        if after is not None and not _is_int(after):
            raise Reject("游标畸形")
        return after


# ---------------------------------------------------------------- 站点

class Station:
    """夜班站点:冻版拉页、验链入账、签发与断源领报。"""

    def __init__(self, db, key):
        self._key = _key_bytes(key)
        self._conn = _connect(db)
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS station_profiles(
                    r TEXT PRIMARY KEY,
                    s TEXT NOT NULL,
                    z INTEGER NOT NULL,
                    h TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS station_tasks(
                    t TEXT PRIMARY KEY,
                    c TEXT NOT NULL,
                    v INTEGER NOT NULL,
                    r TEXT NOT NULL,
                    s TEXT NOT NULL,
                    z INTEGER NOT NULL,
                    h TEXT NOT NULL,
                    cursor TEXT,
                    done INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS station_pulls(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    t TEXT NOT NULL,
                    cursor_in TEXT,
                    item_json TEXT NOT NULL,
                    cursor_out TEXT);
                CREATE TABLE IF NOT EXISTS station_batches(
                    batch INTEGER PRIMARY KEY AUTOINCREMENT,
                    t TEXT NOT NULL UNIQUE,
                    c TEXT NOT NULL,
                    v INTEGER NOT NULL,
                    r TEXT NOT NULL,
                    s TEXT NOT NULL,
                    z INTEGER NOT NULL,
                    h TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    chain_hash TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    over_count INTEGER NOT NULL,
                    ratio TEXT);
                CREATE TABLE IF NOT EXISTS station_batch_rows(
                    batch INTEGER NOT NULL,
                    seq INTEGER NOT NULL,
                    page INTEGER NOT NULL,
                    pos INTEGER NOT NULL,
                    num INTEGER NOT NULL,
                    um TEXT NOT NULL,
                    over INTEGER NOT NULL,
                    PRIMARY KEY(batch, seq));
                """
            )

    def close(self):
        self._conn.close()

    # ---- 口径与任务冻结 ----

    def profile(self, r, s, z, h):
        """定口径:s 正有理,z 整数,h 有理;同名异参拒绝,同件复登免改。"""
        if not isinstance(r, str) or not r:
            raise Reject("口径名r须为非空字符串")
        fs = _frac(s, "s")
        if fs <= 0:
            raise Reject("s 须为正有理数")
        if not _is_int(z):
            raise Reject("z 须为整数")
        fh = _frac(h, "h")
        with self._conn:
            got = self._conn.execute(
                "SELECT s,z,h FROM station_profiles WHERE r=?", (r,)
            ).fetchone()
            if got is not None:
                if (got[0], got[1], got[2]) != (str(fs), z, str(fh)):
                    raise Reject(f"同号异参:口径{r}已固定,拒绝改参")
                return
            self._conn.execute(
                "INSERT INTO station_profiles(r,s,z,h) VALUES(?,?,?,?)",
                (r, str(fs), z, str(fh)),
            )

    def start(self, t, p, c, v, r):
        """冻结任务 t 的栏目版与口径;同号异参拒绝,同件复启免改。"""
        if not isinstance(t, str) or not t:
            raise Reject("任务t须为非空字符串")
        got = self._conn.execute(
            "SELECT c,v,r FROM station_tasks WHERE t=?", (t,)
        ).fetchone()
        if got is not None:
            if (got[0], got[1], got[2]) != (c, v, r):
                raise Reject(f"同号异参:任务{t}已冻结,拒绝改参")
            return
        prof = self._conn.execute(
            "SELECT s,z,h FROM station_profiles WHERE r=?", (r,)
        ).fetchone()
        if prof is None:
            raise Reject(f"未知引用:口径{r}未登记")
        if not isinstance(p, Portal) or not p.has_version(c, v):
            raise Reject(f"未知引用:栏目{c}版{v}未在门户发布")
        with self._conn:
            self._conn.execute(
                "INSERT INTO station_tasks(t,c,v,r,s,z,h) VALUES(?,?,?,?,?,?,?)",
                (t, c, v, r, prof[0], prof[1], prof[2]),
            )

    # ---- 拉页 ----

    def pull(self, t, p, n):
        """拉下一页:同存原页与续点;失读不落库、原位重试;已尽返回 None。"""
        task = self._task(t)
        if task["done"]:
            return None
        if not isinstance(p, Portal):
            raise Reject("p 须为门户")
        item = p.page(task["c"], task["v"], task["cursor"], n)  # 失读则续点不动
        with self._conn:
            self._conn.execute(
                "INSERT INTO station_pulls(t,cursor_in,item_json,cursor_out) VALUES(?,?,?,?)",
                (t, task["cursor"], _canon(item), item["next"]),
            )
            self._conn.execute(
                "UPDATE station_tasks SET cursor=?, done=? WHERE t=?",
                (item["next"], 1 if item["next"] is None else 0, t),
            )
        return item

    def pages(self, t):
        """领存页:按拉取序返回登记原页。"""
        self._task(t)
        return [
            json.loads(row[0])
            for row in self._conn.execute(
                "SELECT item_json FROM station_pulls WHERE t=? ORDER BY seq", (t,)
            )
        ]

    # ---- 验链入账 ----

    def admit(self, t, items, hook=None):
        """仅凭登记、交件与预配 key 验实发同版首尾链;原页与换算同次入账,
        提交前 hook 异常回滚;同任务同件复领批号,异件拒绝。"""
        task = self._task(t)
        if not isinstance(items, list) or not items:
            raise Reject("交件须为非空页件列表")
        try:
            blob = _canon(items)
        except (TypeError, ValueError):
            raise Reject("交件须为 JSON 可序列化")
        got = self._conn.execute(
            "SELECT batch, items_json FROM station_batches WHERE t=?", (t,)
        ).fetchone()
        if got is not None:
            if got[1] == blob:
                return got[0]  # 同任务同件复领批号
            raise Reject("异件拒绝:任务已入账,交件与实收不符")
        self._verify_chain(task, items)
        s, z, h = task["s"], task["z"], task["h"]
        records = []
        for pi, it in enumerate(items):
            for ri, row4 in enumerate(it["rows"]):
                um = Fraction(row4[2]) * s + z  # 原读乘正有理s加整数z,精确微米值
                records.append((pi, ri, row4[1], um, um > h))  # 严格超阈计超限
        total = len(records)
        over_count = sum(1 for rec in records if rec[4])
        ratio = Fraction(over_count, total) if total else None  # 空比 None
        info = {
            "task": t,
            "column": task["c"],
            "version": task["v"],
            "profile": task["r"],
            "total": total,
            "over_count": over_count,
            "ratio": ratio,
        }
        with self._conn:  # 原页与换算同次入账;hook 异常则整体回滚
            cur = self._conn.execute(
                """INSERT INTO station_batches
                   (t,c,v,r,s,z,h,items_json,chain_hash,total,over_count,ratio)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    t, task["c"], task["v"], task["r"], str(s), z, str(h), blob,
                    hashlib.sha256(blob.encode("utf-8")).hexdigest(),
                    total, over_count, None if ratio is None else str(ratio),
                ),
            )
            batch = cur.lastrowid
            for seq, (pi, ri, num, um, over) in enumerate(records):
                self._conn.execute(
                    "INSERT INTO station_batch_rows(batch,seq,page,pos,num,um,over)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (batch, seq, pi, ri, num, str(um), 1 if over else 0),
                )
            info["batch"] = batch
            if hook is not None:
                hook(info)  # 提交前 hook;异常即回滚
        return batch

    def _verify_chain(self, task, items):
        # 1) 逐件验形、验版、验签(预配 key)
        for it in items:
            if not isinstance(it, dict):
                raise Reject("伪页:页件须为 JSON 对象")
            try:
                c, v, rows, nxt, sig = (
                    it["c"], it["v"], it["rows"], it["next"], it["sig"],
                )
            except KeyError:
                raise Reject("伪页:页件缺字段")
            if c != task["c"] or v != task["v"]:
                raise Reject("串版拒绝:页件与任务冻结版不符")
            if not isinstance(rows, list) or any(
                not isinstance(r, (list, tuple)) or len(r) != 4 for r in rows
            ):
                raise Reject("伪页:行畸形")
            core = _canon({"c": c, "v": v, "rows": rows, "next": nxt})
            if not isinstance(sig, str) or not hmac.compare_digest(
                sig, _sig(self._key, core)
            ):
                raise Reject("伪页拒绝:签验失败")
        # 2) 首尾链:首件须为登记起读页,续件须在登记路径上,末件 next 须为 null
        prev = None
        last = len(items) - 1
        for i, it in enumerate(items):
            if _canon(it) not in self._registered(prev):
                if i == 0:
                    raise Reject("伪页拒绝:首件非登记起读页")
                raise Reject("跨路径拼页拒绝:续件不在登记路径上")
            prev = it["next"]
            if prev is None and i != last:
                raise Reject("拼页拒绝:末页之后仍有续件")
        if items[-1]["next"] is not None:
            raise Reject("缺尾拒绝:末件 next 非 null")

    def _registered(self, cursor_in):
        if cursor_in is None:
            cur = self._conn.execute(
                "SELECT item_json FROM station_pulls WHERE cursor_in IS NULL"
            )
        else:
            if not isinstance(cursor_in, str):
                return set()
            cur = self._conn.execute(
                "SELECT item_json FROM station_pulls WHERE cursor_in=?", (cursor_in,)
            )
        return {row[0] for row in cur}

    # ---- 签发与领报 ----

    def sign(self, batch):
        """只签实收;同批复领同签。"""
        rec = self._batch(batch)
        return {
            "batch": rec["batch"],
            "task": rec["t"],
            "signature": _sig(self._key, _canon(self._batch_payload(rec))),
        }

    def lookup(self, ref, expect=None):
        """断源重开领报:冻结口径、原页、行位、换算、编号序超限项及精确比例;
        拒未知引用及同号异参。"""
        if isinstance(ref, dict):
            b = ref.get("batch")
            sig = ref.get("signature")
            if not _is_int(b) or not isinstance(sig, str):
                raise Reject("报号畸形")
            rec = self._batch(b)
            want = _sig(self._key, _canon(self._batch_payload(rec)))
            if not hmac.compare_digest(sig, want):
                raise Reject("同号异参拒绝:报号签验不符")
            if "task" in ref and ref["task"] != rec["t"]:
                raise Reject("同号异参拒绝:任务不符")
        elif _is_int(ref):
            b = ref
            rec = self._batch(b)
        else:
            raise Reject("报号须为批号整数或签发件")
        if expect is not None:
            if not isinstance(expect, dict):
                raise Reject("expect 须为期望项字典")
            mapping = {
                "task": rec["t"], "column": rec["c"],
                "version": rec["v"], "profile": rec["r"],
            }
            for k, v in expect.items():
                if k not in mapping:
                    raise Reject(f"未知期望项:{k}")
                if mapping[k] != v:
                    raise Reject(f"同号异参拒绝:{k}不符")
        items = json.loads(rec["items_json"])
        flat = [row4 for it in items for row4 in it["rows"]]
        metas = self._conn.execute(
            "SELECT seq,page,pos,num,um,over FROM station_batch_rows"
            " WHERE batch=? ORDER BY seq",
            (b,),
        ).fetchall()
        rows_out, over_out = [], []
        for (seq, page, pos, num, um_s, over), row4 in zip(metas, flat):
            entry = {
                "seq": seq, "page": page, "pos": pos,
                "id": row4[0], "num": row4[1],
                "reading": row4[2], "rowver": row4[3],
                "um": Fraction(um_s),
            }
            rows_out.append(entry)
            if over:
                over_out.append(entry)
        over_out.sort(key=lambda e: e["num"])  # 编号序超限项
        return {
            "batch": b,
            "task": rec["t"],
            "column": rec["c"],
            "version": rec["v"],
            "profile": {
                "r": rec["r"], "s": Fraction(rec["s"]),
                "z": rec["z"], "h": Fraction(rec["h"]),
            },
            "pages": items,
            "rows": rows_out,
            "over": over_out,
            "total": rec["total"],
            "over_count": rec["over_count"],
            "ratio": None if rec["ratio"] is None else Fraction(rec["ratio"]),
        }

    # ---- 内部 ----

    def _batch_payload(self, rec):
        return {
            "batch": rec["batch"], "task": rec["t"], "column": rec["c"],
            "version": rec["v"], "profile": rec["r"], "chain": rec["chain_hash"],
            "total": rec["total"], "over": rec["over_count"], "ratio": rec["ratio"],
        }

    def _batch(self, b):
        if not _is_int(b):
            raise Reject("批号须为整数")
        row = self._conn.execute(
            "SELECT batch,t,c,v,r,s,z,h,items_json,chain_hash,total,over_count,ratio"
            " FROM station_batches WHERE batch=?",
            (b,),
        ).fetchone()
        if row is None:
            raise Reject(f"未知引用:批号{b}未入账")
        keys = (
            "batch", "t", "c", "v", "r", "s", "z", "h",
            "items_json", "chain_hash", "total", "over_count", "ratio",
        )
        return dict(zip(keys, row))

    def _task(self, t):
        row = self._conn.execute(
            "SELECT t,c,v,r,s,z,h,cursor,done FROM station_tasks WHERE t=?", (t,)
        ).fetchone()
        if row is None:
            raise Reject(f"未知引用:任务{t}未冻结")
        return {
            "t": row[0], "c": row[1], "v": row[2], "r": row[3],
            "s": Fraction(row[4]), "z": row[5], "h": Fraction(row[6]),
            "cursor": row[7], "done": bool(row[8]),
        }


# ---------------------------------------------------------------- 演示

def _brief(item):
    return {"rows": item["rows"], "next": None if item["next"] is None else "<游标>"}


def _show_reject(fn, note):
    try:
        fn()
    except Reject as e:
        print(f"  {note}:拒绝 -> {e}")
    else:
        print(f"  {note}:未被拒绝(演示异常)")
        raise SystemExit(1)


def cmd_demo():
    import tempfile

    work = tempfile.mkdtemp(prefix="bridge-demo-")
    ppath = os.path.join(work, "portal.db")
    spath = os.path.join(work, "station.db")
    key = "night-shift-key"
    portal = Portal(ppath, key)
    st = Station(spath, key)

    print("== 1. 门户定版:栏目 defl 版1 ==")
    portal.publish("defl", 1, [["A", 1, 4, 1], ["B", 2, 8, 1], ["C", 3, 10, 1]])
    print("  行[测点ID,编号,原读,源行版]: A/1/4/1, B/2/8/1, C/3/10/1")

    print("== 2. 口径与任务冻结:s=1/2, z=1, h=5 ==")
    st.profile("main", Fraction(1, 2), 1, 5)
    st.start("t1", portal, "defl", 1, "main")
    print("  任务 t1 冻结 defl 版1 + 口径 main")

    print("== 3. 拉页(页长2),中途断源、原位重试 ==")
    p1 = st.pull("t1", portal, 2)
    print("  第1页:", _brief(p1))
    portal.close()  # 模拟断源
    try:
        st.pull("t1", portal, 2)
    except Exception as e:
        print(f"  断源失读:{type(e).__name__},续点不动")
    portal = Portal(ppath, key)  # 重开源
    p2 = st.pull("t1", portal, 2)
    print("  原位重试第2页:", _brief(p2))
    print("  再拉(已尽):", st.pull("t1", portal, 2))

    print("== 4. 验链入账:两页 [4,8]/[10],乘1/2加1、阈5 ==")
    got = st.pages("t1")
    batch = st.admit("t1", got)
    rep = st.lookup(batch)
    for r in rep["rows"]:
        mark = " 超限" if r in rep["over"] else ""
        print(f"  编号{r['num']} 原读{r['reading']} -> {r['um']}µm{mark}"
              f" (页{r['page']}位{r['pos']})")
    print(f"  批号:{batch} 仅末行超限,精确比例:{rep['ratio']}")
    print("  同任务同件复领批号:", st.admit("t1", got))
    _show_reject(lambda: st.admit("t1", got[:1]), "同任务异件")

    print("== 5. 拒伪核验 ==")
    st.start("tA", portal, "defl", 1, "main")
    _show_reject(lambda: st.admit("tA", [p1]), "缺尾(仅首页)")
    fake = {"c": "defl", "v": 1,
            "rows": [["A", 1, 4, 1], ["B", 2, 8, 1], ["C", 3, 10, 1]],
            "next": None, "sig": "0" * 64}
    st.start("tB", portal, "defl", 1, "main")
    _show_reject(lambda: st.admit("tB", [fake]), "冒充单页")
    tampered = json.loads(json.dumps(p1))
    tampered["rows"][1][2] = 9
    st.start("tC", portal, "defl", 1, "main")
    _show_reject(lambda: st.admit("tC", [tampered, p2]), "篡改原读")
    portal.publish("defl", 2, [["A", 1, 1, 1]])
    st.start("tV", portal, "defl", 2, "main")
    v2page = st.pull("tV", portal, 9)
    st.start("tD", portal, "defl", 1, "main")
    _show_reject(lambda: st.admit("tD", [p1, v2page]), "串版")
    st.start("tE", portal, "defl", 1, "main")
    _show_reject(lambda: st.admit("tE", [p2]), "跨路径拼页(非首页)")

    print("== 6. 真单页可入另任务 ==")
    portal.publish("strain", 1, [["D", 1, 7, 1]])
    st.start("t2", portal, "strain", 1, "main")
    one = st.pull("t2", portal, 5)
    b2 = st.admit("t2", [one])
    print(f"  单页 {_brief(one)} 入任务 t2,批号:{b2}")

    print("== 7. 空版须真末页,空比 None ==")
    portal.publish("zero", 1, [])
    st.start("t3", portal, "zero", 1, "main")
    ep = st.pull("t3", portal, 4)
    b3 = st.admit("t3", [ep])
    print(f"  空版真末页入账,批号:{b3}")
    fake_empty = {"c": "zero", "v": 1, "rows": [], "next": None, "sig": "0" * 64}
    st.start("tF", portal, "zero", 1, "main")
    _show_reject(lambda: st.admit("tF", [fake_empty]), "冒充空版末页")

    print("== 8. 签发:只签实收,同批复领 ==")
    tok1, tok2 = st.sign(batch), st.sign(batch)
    print("  同批复领一致:", tok1 == tok2)
    _show_reject(lambda: st.sign(999), "签未入账批号")

    print("== 9. 断源重开领报 ==")
    portal.close()
    os.remove(ppath)  # 断源
    st.close()
    st2 = Station(spath, key)  # 重开(仅站库)
    rep = st2.lookup(batch)
    prof = rep["profile"]
    print(f"  冻结口径: r={prof['r']} s={prof['s']} z={prof['z']} h={prof['h']}")
    print("  原页数:", len(rep["pages"]), "行位:", [(r["page"], r["pos"]) for r in rep["rows"]])
    print("  换算:", [str(r["um"]) for r in rep["rows"]])
    print("  编号序超限项:", [(r["num"], str(r["um"])) for r in rep["over"]])
    print("  精确比例:", rep["ratio"])
    print("  空版比例:", st2.lookup(b3)["ratio"])
    _show_reject(lambda: st2.lookup(999), "未知报号")
    forged = {"batch": batch, "task": "t1", "signature": "0" * 64}
    _show_reject(lambda: st2.lookup(forged), "同号异参伪报")
    print("  签发件领报一致:", st2.lookup(tok1)["batch"] == batch)
    print("demo 完成:断源后领报正常")


def main():
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "demo":
        cmd_demo()
        return 0
    print(__doc__)
    print("用法: python3 bridge.py demo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
