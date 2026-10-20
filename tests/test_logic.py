"""核心逻辑单元测试：python -m unittest discover -s tests -v"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autodl_monitor.models import (  # noqa: E402
    GpuStatus,
    Snapshot,
    diff_snapshot,
    matches,
)
from autodl_monitor.render import dwidth, pad, select_rows  # noqa: E402
from autodl_monitor.config import Config  # noqa: E402


def snap(*items: GpuStatus) -> Snapshot:
    s = Snapshot()
    for item in items:
        s.add(item)
    return s


class MatchesTest(unittest.TestCase):
    def test_gpu_and_dc_filter(self) -> None:
        st = GpuStatus("westDC3", "西北B区", "RTX 4090", 2, 100, 1.98)
        self.assertTrue(matches(st, gpu_names=["RTX 4090"], data_centers=["westDC3"], min_idle=1))
        self.assertFalse(matches(st, gpu_names=["A100"]))
        self.assertFalse(matches(st, data_centers=["beijingDC1"]))
        self.assertFalse(matches(st, min_idle=3))

    def test_max_price(self) -> None:
        st = GpuStatus("westDC3", "西北B区", "RTX 4090", 1, 100, 2.5)
        self.assertFalse(matches(st, max_price=2.0))
        self.assertTrue(matches(st, max_price=3.0))
        # 拿不到价格时不因价格过滤
        unknown = st.with_price(None)
        self.assertTrue(matches(unknown, max_price=2.0))


class DiffTest(unittest.TestCase):
    def test_new_availability(self) -> None:
        prev = snap(GpuStatus("westDC3", "西北B区", "RTX 4090", 0, 100))
        cur = snap(GpuStatus("westDC3", "西北B区", "RTX 4090", 2, 100))
        alerts = diff_snapshot(cur, prev)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].kind, "new")
        self.assertEqual(alerts[0].delta, 2)
        self.assertGreater(alerts[0].ts, 0)

    def test_more_available(self) -> None:
        prev = snap(GpuStatus("westDC3", "西北B区", "RTX 4090", 1, 100))
        cur = snap(GpuStatus("westDC3", "西北B区", "RTX 4090", 3, 100))
        alerts = diff_snapshot(cur, prev)
        self.assertEqual([a.kind for a in alerts], ["more"])
        self.assertEqual(alerts[0].delta, 2)

    def test_no_alert_when_unchanged(self) -> None:
        prev = snap(GpuStatus("westDC3", "西北B区", "RTX 4090", 1, 100))
        cur = snap(GpuStatus("westDC3", "西北B区", "RTX 4090", 1, 100))
        self.assertEqual(diff_snapshot(cur, prev), [])

    def test_no_alert_when_gone(self) -> None:
        prev = snap(GpuStatus("westDC3", "西北B区", "RTX 4090", 2, 100))
        cur = snap(GpuStatus("westDC3", "西北B区", "RTX 4090", 0, 100))
        self.assertEqual(diff_snapshot(cur, prev), [])

    def test_startup(self) -> None:
        cur = snap(GpuStatus("westDC3", "西北B区", "RTX 4090", 1, 100))
        self.assertEqual(len(diff_snapshot(cur, None, alert_on_start=True)), 1)
        self.assertEqual(diff_snapshot(cur, None, alert_on_start=False), [])

    def test_filter_applies(self) -> None:
        prev = snap(GpuStatus("westDC3", "西北B区", "A100", 0, 10))
        cur = snap(GpuStatus("westDC3", "西北B区", "A100", 1, 10))
        self.assertEqual(diff_snapshot(cur, prev, gpu_names=["RTX 4090"]), [])


class RenderTest(unittest.TestCase):
    def test_width(self) -> None:
        self.assertEqual(dwidth("abc"), 3)
        self.assertEqual(dwidth("西北B区"), 7)
        self.assertEqual(dwidth("RTX 4090"), 8)

    def test_pad(self) -> None:
        self.assertEqual(dwidth(pad("西北", 6)), 6)
        self.assertEqual(dwidth(pad("abc", 6, "right")), 6)

    def test_select_rows(self) -> None:
        cfg = Config()
        s = snap(
            GpuStatus("westDC3", "西北B区", "RTX 4090", 1, 10),
            GpuStatus("westDC3", "西北B区", "RTX 3090", 0, 10),
        )
        rows = select_rows(s, cfg)
        self.assertEqual([r.gpu_name for r in rows], ["RTX 4090"])

        cfg.gpu = ["RTX 3090"]
        rows = select_rows(s, cfg)
        self.assertEqual([r.gpu_name for r in rows], ["RTX 3090"])

        cfg.show_all = True
        cfg.gpu = []
        self.assertEqual(len(select_rows(s, cfg)), 2)


if __name__ == "__main__":
    unittest.main()
