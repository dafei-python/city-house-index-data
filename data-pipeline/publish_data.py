#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成/刷新数据清单 manifest.json（带 data_version 与每个文件的 sha256）。

两种用法：
  1) 本地发版：把 App 内置数据复制到仓库 data/ 目录，并同时刷新两边的 manifest
       python3 data-pipeline/publish_data.py
  2) GitHub Actions：抓取脚本已经把新月份写进 data/，只在该目录重建 manifest
       python3 data-pipeline/publish_data.py --in-place --data-dir data

data_version 规则：取 generated_at 的 UTC 时间戳 YYYYMMDDHHMMSS（例如 2026-11-07 08:25:30 → 20261107082530）。
- 单调递增：日期越新版本越大，旧版本永远小于新版本；
- 成交量每天更新但 latest_month 不变，若沿用「月份*100+1」的旧规则，App 永远收不到日更数据，
  所以版本号必须能反映「这份数据是哪一刻生成的」；
- 数据没变时，GitHub Actions 每天仍会写一次运行心跳（last_*_run_at）并把 generated_at 推到当天，
  这样仓库和 App 都能看出「今天跑过了、只是没有新数据」，同时同一天的补跑不会重复写。
"""
import argparse
import datetime
import hashlib
import json
import re
import shutil
from pathlib import Path

AUX_FILES = ["base_prices.json", "provinces.json", "deals.json"]
MONTH_GLOB = "index_70cities_*.json"
DATE_RE = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")

# 每个 workflow 只写自己的运行心跳字段：同一天内重复运行不会再改，避免补跑产生重复提交
TIME_FIELD = {"index": "last_index_run_at", "deals": "last_deals_run_at"}
RESULT_FIELD = {"index": "last_index_result", "deals": "last_deals_result"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_months(directory: Path):
    months = []
    for f in sorted(directory.glob(MONTH_GLOB), reverse=True):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            month = payload.get("month") or f.stem.replace("index_70cities_", "")
        except Exception:
            month = f.stem.replace("index_70cities_", "")
        months.append({"month": month, "file": f.name})
    months.sort(key=lambda m: m["month"], reverse=True)
    return months


def _norm_date(text) -> str:
    """"2026/11/6"、"2026-11-6" → "2026-11-06"，认不出就返回空串。"""
    m = DATE_RE.search(text or "")
    if not m:
        return ""
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))


def deals_latest(directory: Path) -> str:
    """成交量数据里最大的「数据日期」，用于 App 提示「成交量更新到哪一天」。"""
    path = directory / "deals.json"
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    best = ""
    for city in payload.get("cities") or []:
        best = max(best, _norm_date(city.get("period_end")))
        for row in city.get("rows") or []:
            best = max(best, _norm_date(row.get("date")))
    return best


def build_manifest(directory: Path) -> dict:
    months = collect_months(directory)
    if not months:
        raise SystemExit("目录里没有任何 index_70cities_*.json：%s" % directory)
    latest = months[0]["month"]

    files = []
    for f in sorted(directory.iterdir()):
        if f.suffix != ".json" or f.name == "manifest.json":
            continue
        files.append({"file": f.name, "sha256": sha256(f), "size": f.stat().st_size})

    return {
        "schema_version": 1,
        "data_version": 0,  # 在 write_manifest 里按 generated_at 计算
        "latest_month": latest,
        "deals_latest": deals_latest(directory),
        "source": "国家统计局 70 个大中城市商品住宅销售价格变动情况",
        "source_column": "新建商品住宅销售价格指数 / 二手住宅销售价格指数",
        "months": months,
        "files": files,
    }


def version_of(generated_at: str) -> int:
    """generated_at（UTC，YYYY-MM-DDTHH:MM:SSZ）→ 20261107082530 这样的单调递增整数。

    精确到秒：指数和成交量是两个独立 workflow，若同分钟提交，分钟级版本号会撞车，
    App 会误判成「已是最新」而漏掉新月份。
    """
    try:
        dt = datetime.datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        dt = datetime.datetime.now(datetime.timezone.utc)
    return int(dt.strftime("%Y%m%d%H%M%S"))


def write_manifest(directory: Path, manifest: dict, previous, run_kind: str = "manual",
                   result=None) -> bool:
    """写入 manifest，并记录「这次是谁、什么时候跑过」。

    两种写入模式：
    · run_kind = "manual"（本地发版）：内容没变就沿用旧 generated_at，版本号不动；
    · run_kind = "index"/"deals"（GitHub Actions）：每次运行都写心跳字段，
      同一 workflow 同一天只写一次 —— 补跑/重试不会产生重复提交，
      但仓库里一定能看到「今天跑过了」，从而区分「没跑」和「跑了没新数」。

    返回是否产生了需要提交的变化。
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    prev = previous or {}

    old_files = [(f["file"], f["sha256"]) for f in (prev.get("files") or [])]
    new_files = [(f["file"], f["sha256"]) for f in (manifest.get("files") or [])]
    data_changed = old_files != new_files or prev.get("latest_month") != manifest.get("latest_month")
    if (prev.get("deals_latest") or "") != (manifest.get("deals_latest") or ""):
        data_changed = True

    if run_kind in TIME_FIELD:
        time_field = TIME_FIELD[run_kind]
        result_field = RESULT_FIELD[run_kind]
        already_today = (prev.get(time_field) or "")[:10] == stamp[:10]
        if data_changed or not already_today:
            manifest[time_field] = stamp
            manifest[result_field] = result or ("changed" if data_changed else "no_change")
            manifest["generated_at"] = stamp
        else:
            # 今天已经记过心跳：原样沿用，避免补跑重复提交
            manifest[time_field] = prev.get(time_field)
            manifest[result_field] = prev.get(result_field)
            manifest["generated_at"] = prev.get("generated_at") or stamp

        index_at = manifest.get("last_index_run_at") or ""
        deals_at = manifest.get("last_deals_run_at") or ""
        manifest["last_run_at"] = max(index_at, deals_at) or manifest.get("generated_at") or stamp
        manifest["last_run_kind"] = "index" if index_at >= deals_at else "deals"
    else:
        # 本地发版：不写运行心跳，只保证内容变化时版本号往前走
        manifest["generated_at"] = stamp if data_changed else (prev.get("generated_at") or stamp)
        for field in list(TIME_FIELD.values()) + list(RESULT_FIELD.values()):
            if prev.get(field) is not None:
                manifest[field] = prev.get(field)
        for field in ("last_run_at", "last_run_kind"):
            if prev.get(field) is not None:
                manifest[field] = prev.get(field)

    manifest["data_version"] = version_of(manifest["generated_at"])
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data_changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-assets", default="app/src/main/assets/data",
                    help="App 内置数据目录（本地发版时的源）")
    ap.add_argument("--data-dir", default="data", help="仓库公开数据目录")
    ap.add_argument("--in-place", action="store_true",
                    help="只重建指定目录的 manifest（GitHub Actions 模式）")
    ap.add_argument("--run-kind", default="manual", choices=["manual", "index", "deals"],
                    help="谁在跑：index / deals 会把运行心跳写进 manifest，manual 不写")
    ap.add_argument("--result", default=None,
                    help="覆盖心跳里的结果（例如不在发布窗口时写 skipped）")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    previous = None
    if (data_dir / "manifest.json").exists():
        previous = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))

    if not args.in_place:
        assets = Path(args.from_assets)
        if not assets.exists():
            raise SystemExit("内置数据目录不存在：%s" % assets)

        # 月度文件 + 辅助文件全部同步过去（只增不删，历史月份要能一直查证）
        for f in assets.glob("*.json"):
            if f.name == "manifest.json":
                continue
            shutil.copy2(f, data_dir / f.name)
        for name in AUX_FILES:
            if (assets / name).exists() and not (data_dir / name).exists():
                shutil.copy2(assets / name, data_dir / name)

    manifest = build_manifest(data_dir)
    changed = write_manifest(data_dir, manifest, previous, args.run_kind, args.result)

    # 本地发版时，把与公网一致的 manifest 也写回 App 内置数据，保证版本号口径一致
    if not args.in_place:
        (Path(args.from_assets) / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print("data_version=%s, latest_month=%s, deals_latest=%s, files=%d, data_changed=%s, "
          "last_run=%s(%s)"
          % (manifest["data_version"], manifest["latest_month"], manifest.get("deals_latest"),
             len(manifest["files"]), changed, manifest.get("last_run_at"), manifest.get("last_run_kind")))


if __name__ == "__main__":
    main()
