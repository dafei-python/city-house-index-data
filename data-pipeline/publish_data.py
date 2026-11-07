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
- 内容没变时沿用旧 generated_at，版本号不变，避免空跑产生无意义提交。
"""
import argparse
import datetime
import hashlib
import json
import shutil
from pathlib import Path

AUX_FILES = ["base_prices.json", "provinces.json", "deals.json"]
MONTH_GLOB = "index_70cities_*.json"


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


def write_manifest(directory: Path, manifest: dict, previous) -> bool:
    """内容（文件 hash 列表）没变就保留旧的 generated_at，返回是否有实质变化。"""
    changed = True
    if previous is not None:
        old_files = previous.get("files") or []
        new_files = manifest.get("files") or []
        same = [(f["file"], f["sha256"]) for f in old_files] == [(f["file"], f["sha256"]) for f in new_files]
        if same and previous.get("latest_month") == manifest.get("latest_month"):
            changed = False
            manifest["generated_at"] = previous.get("generated_at")
    if manifest.get("generated_at") is None:
        manifest["generated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["data_version"] = version_of(manifest["generated_at"])
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-assets", default="app/src/main/assets/data",
                    help="App 内置数据目录（本地发版时的源）")
    ap.add_argument("--data-dir", default="data", help="仓库公开数据目录")
    ap.add_argument("--in-place", action="store_true",
                    help="只重建指定目录的 manifest（GitHub Actions 模式）")
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
    changed = write_manifest(data_dir, manifest, previous)

    # 本地发版时，把与公网一致的 manifest 也写回 App 内置数据，保证版本号口径一致
    if not args.in_place:
        (Path(args.from_assets) / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print("data_version = %s, latest_month = %s, files = %d, changed = %s"
          % (manifest["data_version"], manifest["latest_month"], len(manifest["files"]), changed))


if __name__ == "__main__":
    main()
