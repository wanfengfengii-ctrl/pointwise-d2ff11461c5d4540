"""test_bridge.py — bridge.py 回归与拒伪核验。"""

import json
import os
import sqlite3
import tempfile
import unittest
from fractions import Fraction

from bridge import Portal, Station, Reject

KEY = "night-key"
ROWS = [["A", 1, 4, 1], ["B", 2, 8, 1], ["C", 3, 10, 1]]


class Flow(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.ppath = os.path.join(self.work, "p.db")
        self.spath = os.path.join(self.work, "s.db")
        self.portal = Portal(self.ppath, KEY)
        self.st = Station(self.spath, KEY)

    def tearDown(self):
        for obj in (self.portal, self.st):
            try:
                obj.close()
            except Exception:
                pass

    def publish_main(self):
        self.portal.publish("defl", 1, ROWS)

    def start_main(self, t="t1"):
        self.st.profile("main", Fraction(1, 2), 1, 5)
        self.st.start(t, self.portal, "defl", 1, "main")

    def pull_all(self, t="t1", n=2):
        out = []
        while True:
            it = self.st.pull(t, self.portal, n)
            if it is None:
                break
            out.append(it)
        return out


# ---------------------------------------------------------------- 门户

class TestPortal(Flow):
    def test_page_pagination(self):
        self.publish_main()
        p1 = self.portal.page("defl", 1, None, 2)
        self.assertEqual([r[2] for r in p1["rows"]], [4, 8])
        self.assertIsNotNone(p1["next"])
        p2 = self.portal.page("defl", 1, p1["next"], 2)
        self.assertEqual([r[2] for r in p2["rows"]], [10])
        self.assertIsNone(p2["next"])  # 末页 next=null

    def test_page_size_may_vary(self):
        self.publish_main()
        q1 = self.portal.page("defl", 1, None, 1)
        self.assertEqual([r[2] for r in q1["rows"]], [4])
        q2 = self.portal.page("defl", 1, q1["next"], 5)  # 页长可换
        self.assertEqual([r[2] for r in q2["rows"]], [8, 10])
        self.assertIsNone(q2["next"])
        one = self.portal.page("defl", 1, None, 3)
        self.assertIsNone(one["next"])
        self.assertEqual(len(one["rows"]), 3)

    def test_page_item_shape(self):
        self.publish_main()
        it = self.portal.page("defl", 1, None, 2)
        self.assertIn("rows", it)
        self.assertIn("next", it)
        json.dumps(it)  # JSON 页件

    def test_empty_version_single_last_page(self):
        self.portal.publish("zero", 1, [])
        it = self.portal.page("zero", 1, None, 4)
        self.assertEqual(it["rows"], [])
        self.assertIsNone(it["next"])

    def test_publish_rules(self):
        self.publish_main()
        self.portal.publish("defl", 1, list(reversed(ROWS)))  # 同件复登(乱序同集)
        with self.assertRaises(Reject):  # 同号异参
            self.portal.publish("defl", 1, [["A", 1, 4, 1]])
        with self.assertRaises(Reject):  # 版内ID重复
            self.portal.publish("defl", 2, [["A", 1, 4, 1], ["A", 2, 5, 1]])
        with self.assertRaises(Reject):  # 版内编号重复
            self.portal.publish("defl", 2, [["A", 1, 4, 1], ["B", 1, 5, 1]])
        with self.assertRaises(Reject):  # 原读非整数
            self.portal.publish("defl", 2, [["A", 1, 4.5, 1]])
        with self.assertRaises(Reject):  # 行缺列
            self.portal.publish("defl", 2, [["A", 1, 4]])
        with self.assertRaises(Reject):  # 布尔冒充实数
            self.portal.publish("defl", 2, [["A", True, 4, 1]])

    def test_page_unknown_reference(self):
        with self.assertRaises(Reject):
            self.portal.page("defl", 1, None, 2)
        with self.assertRaises(Reject):
            self.portal.page("defl", 1, None, 0)
        with self.assertRaises(Reject):
            self.portal.page("defl", 1, None, -3)
        with self.assertRaises(Reject):
            self.portal.page("defl", 1, None, "2")

    def test_wrong_version_cursor(self):
        self.publish_main()
        self.portal.publish("defl", 2, [["A", 1, 1, 1], ["B", 2, 2, 1]])
        self.portal.publish("other", 1, [["A", 1, 1, 1], ["B", 2, 2, 1]])
        cur = self.portal.page("defl", 1, None, 1)["next"]
        with self.assertRaises(Reject):  # 错版游标
            self.portal.page("defl", 2, cur, 1)
        with self.assertRaises(Reject):  # 错栏目游标
            self.portal.page("other", 1, cur, 1)
        with self.assertRaises(Reject):  # 伪造游标
            self.portal.page("defl", 1, cur[:-1] + ("A" if cur[-1] != "A" else "B"), 1)
        with self.assertRaises(Reject):  # 畸形游标
            self.portal.page("defl", 1, "not-a-cursor", 1)
        with self.assertRaises(Reject):
            self.portal.page("defl", 1, 123, 1)


# ---------------------------------------------------------------- 口径与冻结

class TestProfileStart(Flow):
    def test_profile_rules(self):
        self.st.profile("main", Fraction(1, 2), 1, 5)
        self.st.profile("main", "1/2", 1, 5)  # 同件复登
        with self.assertRaises(Reject):  # 同号异参
            self.st.profile("main", Fraction(1, 3), 1, 5)
        with self.assertRaises(Reject):  # s 须正
            self.st.profile("bad", 0, 1, 5)
        with self.assertRaises(Reject):
            self.st.profile("bad", -1, 1, 5)
        with self.assertRaises(Reject):  # z 须整数
            self.st.profile("bad", 1, 1.5, 5)
        with self.assertRaises(Reject):  # 拒浮点保精确
            self.st.profile("bad", 0.5, 1, 5)

    def test_start_rules(self):
        self.publish_main()
        self.st.profile("main", Fraction(1, 2), 1, 5)
        with self.assertRaises(Reject):  # 未知口径
            self.st.start("t1", self.portal, "defl", 1, "nope")
        with self.assertRaises(Reject):  # 未知版
            self.st.start("t1", self.portal, "defl", 9, "main")
        with self.assertRaises(Reject):  # 未知栏目
            self.st.start("t1", self.portal, "nope", 1, "main")
        self.st.start("t1", self.portal, "defl", 1, "main")
        self.st.start("t1", self.portal, "defl", 1, "main")  # 同件复启
        with self.assertRaises(Reject):  # 同号异参
            self.st.start("t1", self.portal, "defl", 2, "main")
        with self.assertRaises(Reject):
            self.st.start("t1", self.portal, "defl", 1, "other")


# ---------------------------------------------------------------- 拉页

class TestPull(Flow):
    def test_pull_and_pages(self):
        self.publish_main()
        self.start_main()
        got = self.pull_all(n=2)
        self.assertEqual(len(got), 2)
        self.assertEqual([r[2] for r in got[0]["rows"]], [4, 8])
        self.assertEqual([r[2] for r in got[1]["rows"]], [10])
        self.assertIsNone(got[1]["next"])
        self.assertEqual(self.st.pages("t1"), got)  # 领存页
        self.assertIsNone(self.st.pull("t1", self.portal, 2))  # 已尽

    def test_pull_unknown_task(self):
        with self.assertRaises(Reject):
            self.st.pull("nope", self.portal, 2)
        with self.assertRaises(Reject):
            self.st.pages("nope")

    def test_pull_retry_same_position(self):
        """失读原位重试:断源失败不落库,续点不动。"""
        self.publish_main()
        self.start_main()
        p1 = self.st.pull("t1", self.portal, 2)
        self.portal.close()  # 断源
        with self.assertRaises(Exception):
            self.st.pull("t1", self.portal, 2)
        self.assertEqual(len(self.st.pages("t1")), 1)  # 失败未入账
        self.portal = Portal(self.ppath, KEY)  # 重开源
        p2 = self.st.pull("t1", self.portal, 2)  # 原位重试
        self.assertEqual([r[2] for r in p2["rows"]], [10])
        self.assertEqual([r[2] for r in self.st.pages("t1")[0]["rows"]],
                         [r[2] for r in p1["rows"]])
        self.assertEqual(len(self.st.pages("t1")), 2)


# ---------------------------------------------------------------- 验链入账

class TestAdmit(Flow):
    def test_admit_and_lookup(self):
        self.publish_main()
        self.start_main()
        got = self.pull_all(n=2)
        batch = self.st.admit("t1", got)
        self.assertEqual(batch, 1)
        self.assertEqual(self.st.admit("t1", got), batch)  # 同任务同件复领批号
        rep = self.st.lookup(batch)
        self.assertEqual([str(r["um"]) for r in rep["rows"]], ["3", "5", "6"])
        self.assertEqual([r["num"] for r in rep["over"]], [3])  # 仅末行超限
        self.assertEqual(rep["ratio"], Fraction(1, 3))  # 精确比例
        self.assertEqual(rep["profile"]["s"], Fraction(1, 2))
        self.assertEqual(rep["profile"]["z"], 1)
        self.assertEqual(rep["profile"]["h"], Fraction(5))
        self.assertEqual(rep["pages"], got)  # 原页
        self.assertEqual([(r["page"], r["pos"]) for r in rep["rows"]],
                         [(0, 0), (0, 1), (1, 0)])  # 行位

    def test_admit_reject_different_items(self):
        self.publish_main()
        self.start_main()
        got = self.pull_all(n=2)
        self.st.admit("t1", got)
        with self.assertRaises(Reject):  # 异件拒绝
            self.st.admit("t1", got[:1])
        # 另一页长拉出的真链亦为异件
        self.start_main("t2")
        alt = self.pull_all(t="t2", n=1)
        self.assertEqual(len(alt), 3)
        with self.assertRaises(Reject):
            self.st.admit("t1", alt)

    def test_missing_tail(self):
        self.publish_main()
        self.start_main()
        got = self.pull_all(n=2)
        self.start_main("tA")
        with self.assertRaises(Reject):  # 缺尾
            self.st.admit("tA", got[:1])
        with self.assertRaises(Reject):  # 空交件
            self.st.admit("tA", [])
        with self.assertRaises(Reject):  # 非列表
            self.st.admit("tA", "pages")

    def test_version_mix(self):
        self.publish_main()
        self.portal.publish("defl", 2, [["A", 1, 1, 1]])
        self.st.profile("main", Fraction(1, 2), 1, 5)
        self.st.start("tv", self.portal, "defl", 2, "main")
        v2 = self.st.pull("tv", self.portal, 9)
        self.start_main()
        got = self.pull_all(n=2)
        self.start_main("tA")
        with self.assertRaises(Reject):  # 串版
            self.st.admit("tA", [got[0], v2])
        with self.assertRaises(Reject):  # 整链串版
            self.st.admit("tA", [v2])

    def test_fake_pages(self):
        self.publish_main()
        self.start_main()
        got = self.pull_all(n=2)
        # 冒充单页:捏造整版一页
        fake = {"c": "defl", "v": 1, "rows": ROWS, "next": None, "sig": "0" * 64}
        self.start_main("tA")
        with self.assertRaises(Reject):
            self.st.admit("tA", [fake])
        # 篡改原读,沿用原签
        tampered = json.loads(json.dumps(got[0]))
        tampered["rows"][1][2] = 9
        self.start_main("tB")
        with self.assertRaises(Reject):
            self.st.admit("tB", [tampered, got[1]])
        # 他钥签的真形页
        import hmac, hashlib
        core = {"c": "defl", "v": 1, "rows": ROWS, "next": None}
        blob = json.dumps(core, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
        evil = dict(core)
        evil["sig"] = hmac.new(b"evil", blob.encode(), hashlib.sha256).hexdigest()
        self.start_main("tC")
        with self.assertRaises(Reject):
            self.st.admit("tC", [evil])
        # 站点密钥不符,真页亦伪
        st2 = Station(os.path.join(self.work, "s2.db"), "wrong-key")
        st2.profile("main", Fraction(1, 2), 1, 5)
        st2.start("t1", self.portal, "defl", 1, "main")
        st2.pull("t1", self.portal, 3)
        with self.assertRaises(Reject):
            st2.admit("t1", st2.pages("t1"))
        st2.close()

    def test_cross_path_splice(self):
        self.publish_main()
        self.st.profile("main", Fraction(1, 2), 1, 5)
        self.st.start("t1", self.portal, "defl", 1, "main")
        path2 = self.pull_all(t="t1", n=2)  # [4,8],[10]
        self.st.start("t2", self.portal, "defl", 1, "main")
        path1 = self.pull_all(t="t2", n=1)  # [4],[8],[10]
        self.st.start("t3", self.portal, "defl", 1, "main")
        with self.assertRaises(Reject):  # 非首页冒充首件
            self.st.admit("t3", [path2[1]])
        with self.assertRaises(Reject):  # 重复首件
            self.st.admit("t3", [path2[0], path2[0]])
        with self.assertRaises(Reject):  # 跨路径拼页:首页后接他径页
            self.st.admit("t3", [path1[0], path2[0]])
        with self.assertRaises(Reject):  # 末页后续件
            self.st.admit("t3", [path2[0], path2[1], path2[0]])
        # 真链可入另任务(同站登记)
        b = self.st.admit("t3", path2)
        self.assertEqual(self.st.lookup(b)["ratio"], Fraction(1, 3))
        self.st.start("t4", self.portal, "defl", 1, "main")
        self.st.admit("t4", path1)  # 页长1的真链亦可

    def test_empty_version_true_last_page(self):
        self.portal.publish("zero", 1, [])
        self.st.profile("main", Fraction(1, 2), 1, 5)
        self.st.start("tz", self.portal, "zero", 1, "main")
        ep = self.st.pull("tz", self.portal, 4)
        self.assertEqual(ep["rows"], [])
        self.assertIsNone(ep["next"])
        b = self.st.admit("tz", [ep])  # 空版真末页
        rep = self.st.lookup(b)
        self.assertEqual(rep["total"], 0)
        self.assertIsNone(rep["ratio"])  # 空比 None
        self.assertEqual(rep["over"], [])
        fake = {"c": "zero", "v": 1, "rows": [], "next": None, "sig": "0" * 64}
        self.st.start("tz2", self.portal, "zero", 1, "main")
        with self.assertRaises(Reject):  # 冒充空版末页
            self.st.admit("tz2", [fake])

    def test_hook_rollback(self):
        self.publish_main()
        self.start_main()
        got = self.pull_all(n=2)
        seen = {}

        def bad_hook(info):
            seen.update(info)
            raise RuntimeError("夜班复核不通过")

        with self.assertRaises(RuntimeError):
            self.st.admit("t1", got, hook=bad_hook)
        self.assertEqual(seen["total"], 3)
        self.assertEqual(seen["over_count"], 1)
        self.assertEqual(seen["ratio"], Fraction(1, 3))
        with self.assertRaises(Reject):  # 回滚后无批
            self.st.sign(1)
        batch = self.st.admit("t1", got)  # 重交可入
        self.assertEqual(batch, 1)

    def test_hook_ok(self):
        self.publish_main()
        self.start_main()
        got = self.pull_all(n=2)
        seen = {}
        batch = self.st.admit("t1", got, hook=lambda info: seen.update(info))
        self.assertEqual(seen["batch"], batch)


# ---------------------------------------------------------------- 精确换算

class TestExactMath(Flow):
    def test_exact_fraction_and_strict_threshold(self):
        self.portal.publish("m", 1, [["A", 1, 1, 1], ["B", 2, 2, 1]])
        self.st.profile("third", Fraction(1, 3), 1, Fraction(4, 3))
        self.st.start("t", self.portal, "m", 1, "third")
        got = self.pull_all(t="t", n=5)
        b = self.st.admit("t", got)
        rep = self.st.lookup(b)
        self.assertEqual(rep["rows"][0]["um"], Fraction(4, 3))  # 恰阈不超
        self.assertEqual(rep["rows"][1]["um"], Fraction(5, 3))  # 严格超
        self.assertEqual([r["num"] for r in rep["over"]], [2])
        self.assertEqual(rep["ratio"], Fraction(1, 2))

    def test_over_sorted_by_num(self):
        rows = [["P" + str(i), i, 9, 1] for i in (5, 3, 4, 1, 2)]
        self.portal.publish("m", 1, rows)
        self.st.profile("main", Fraction(1, 2), 1, 5)
        self.st.start("t", self.portal, "m", 1, "main")
        got = self.pull_all(t="t", n=2)
        rep = self.st.lookup(self.st.admit("t", got))
        self.assertEqual([r["num"] for r in rep["over"]], [1, 2, 3, 4, 5])
        self.assertEqual(rep["ratio"], Fraction(1, 1))


# ---------------------------------------------------------------- 签发与领报

class TestSignLookup(Flow):
    def _batch(self):
        self.publish_main()
        self.start_main()
        got = self.pull_all(n=2)
        return self.st.admit("t1", got), got

    def test_sign_rules(self):
        batch, _ = self._batch()
        t1 = self.st.sign(batch)
        t2 = self.st.sign(batch)
        self.assertEqual(t1, t2)  # 同批复领
        self.assertEqual(t1["batch"], batch)
        with self.assertRaises(Reject):  # 只签实收
            self.st.sign(999)
        with self.assertRaises(Reject):
            self.st.sign("1")

    def test_lookup_refs(self):
        batch, _ = self._batch()
        tok = self.st.sign(batch)
        self.assertEqual(self.st.lookup(tok)["batch"], batch)  # 签发件领报
        with self.assertRaises(Reject):  # 未知引用
            self.st.lookup(999)
        with self.assertRaises(Reject):
            self.st.lookup("1")
        forged = {"batch": batch, "task": "t1", "signature": "0" * 64}
        with self.assertRaises(Reject):  # 同号异参
            self.st.lookup(forged)
        wrong_task = dict(tok, task="nope")
        with self.assertRaises(Reject):
            self.st.lookup(wrong_task)
        self.assertEqual(
            self.st.lookup(batch, expect={"task": "t1", "version": 1})["batch"], batch)
        with self.assertRaises(Reject):  # 同号异参
            self.st.lookup(batch, expect={"version": 2})
        with self.assertRaises(Reject):
            self.st.lookup(batch, expect={"nope": 1})

    def test_reopen_after_source_lost(self):
        """断源重开:领报、存页、复领皆凭站库。"""
        batch, got = self._batch()
        tok = self.st.sign(batch)
        self.portal.close()
        os.remove(self.ppath)  # 断源
        self.st.close()
        st2 = Station(self.spath, KEY)  # 重开
        rep = st2.lookup(batch)
        self.assertEqual(rep["ratio"], Fraction(1, 3))
        self.assertEqual(rep["pages"], got)
        self.assertEqual(st2.pages("t1"), got)
        self.assertEqual(st2.admit("t1", got), batch)  # 复领同批号
        self.assertEqual(st2.sign(batch), tok)  # 同批复领
        with self.assertRaises(Reject):
            st2.lookup(999)
        st2.close()


# ---------------------------------------------------------------- 单页

class TestSinglePage(Flow):
    def test_genuine_single_page_other_task(self):
        """拒冒充单页,真单页可入另任务。"""
        self.publish_main()
        self.st.profile("main", Fraction(1, 2), 1, 5)
        self.st.start("t1", self.portal, "defl", 1, "main")
        one = self.st.pull("t1", self.portal, 9)  # 真单页(整版一页)
        self.assertIsNone(one["next"])
        self.st.start("t2", self.portal, "defl", 1, "main")
        b = self.st.admit("t2", [one])  # 入另任务
        self.assertEqual(self.st.lookup(b)["total"], 3)
        fake = dict(one)
        fake["rows"] = one["rows"][:2]  # 冒充单页
        self.st.start("t3", self.portal, "defl", 1, "main")
        with self.assertRaises(Reject):
            self.st.admit("t3", [fake])


if __name__ == "__main__":
    unittest.main()
