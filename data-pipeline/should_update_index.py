#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
判断「70 城房价指数」今天是否值得抓。

背景：国家统计局每月 15–19 日发布上一个月的数据。所以：
- 每月 1–14 日：官方不可能有新月份 → 直接跳过，不下载、不提交；
- 每月 15 日以后：目标月份 = 上一个月；若本地 manifest 的 latest_month 已经是这个月，
  说明已经抓到过 → 同样跳过，避免 GitHub Actions 每天空跑产生一次提交。

退出码：
  0 = 需要抓取（今天可能真的有新数据）
  1 = 跳过（没有新月份可抓）
"""
import argparse
import datetime
import json
from pathlib import Path


def target_month(today: datetime.date) -> str:
    """官方今天最多已经发布到哪个月（返回 YYYY-MM）。"""
    # 15 日之前，上个月的数据还没发布，最新只可能是上上个月
    back = 1 if today.day >= 15 else 2
    d = today.replace(day=1)
    for _ in range(back):
        d = (d - datetime.timedelta(days=1)).replace(day=1)
    return d.strftime("%Y-%m")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--today", default=None, help="调试用，YYYY-MM-DD")
    args = ap.parse_args()

    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    path = Path(args.data_dir) / "manifest.json"
    if not path.exists():
        print("本地没有 manifest，按需要抓取处理")
        return 0

    manifest = json.loads(path.read_text(encoding="utf-8"))
    latest = manifest.get("latest_month") or ""
    target = target_month(today)

    if latest >= target:
        print(f"跳过：本地 {latest} 已覆盖官方可发布的最新月份 {target}（今天 {today}）")
        return 1

    print(f"需要抓取：今天 {today}，官方应有 {target}，本地仍是 {latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
