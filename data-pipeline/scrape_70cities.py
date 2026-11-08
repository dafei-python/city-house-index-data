#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取国家统计局「70 个大中城市商品住宅销售价格变动情况」月度发布页，解析成 JSON。

设计要点：
1. 不使用 data.stats.gov.cn 的 API（该接口对非授权来源返回 403 UrlACL），只解析统计局官网
   信息公开栏目（xxgk）的静态 HTML 页面。
2. 每月页面里的表格数量会变化（表 3/表 4 可能被拆成「（一）（二）」两个 <table>），
   因此按表标题定位（表1 新建 / 表2 二手），不按表格索引。
3. 抓取结果写 assets/data/index_70cities_YYYY-MM.json，供 Android App 打包进 assets。
4. 所有页面缓存到 data-pipeline/cache/，可重复运行，重复运行不会重复下载。

用法：
    python3 scrape_70cities.py                 # 抓最近 14 个月
    python3 scrape_70cities.py --months 24     # 抓最近 24 个月
    python3 scrape_70cities.py --month 2026-09 # 只处理指定月份
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

from bs4 import BeautifulSoup

BASE = "https://www.stats.gov.cn"
COLUMN = BASE + "/xxgk/sjfb/zxfb2020/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache")
OUT_ROWS = os.path.join(ROOT, "out", "rows")
OUT_ANDROID = os.path.abspath(os.path.join(ROOT, "..", "app", "src", "main", "assets", "data"))

TITLE_RE = re.compile(r"(20\d{2})年(\d{1,2})月份70个大中城市商品住宅销售价格变动情况")
TABLE_TITLE_RE = re.compile(r"表\s*([0-9一二三四])[：:]\s*(20\d{2})年(\d{1,2})月70个大中城市(.*?)销售价格(分类)?指数")


def log(msg):
    print(msg, flush=True)


def cached_get(url, sleep=1.0, retries=3, force=False):
    """文章页默认走本地缓存；栏目列表页 force=True，避免 CDN 旧快照漏掉最新一期。"""
    os.makedirs(CACHE, exist_ok=True)
    name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".html"
    path = os.path.join(CACHE, name)
    if not force and os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": COLUMN,
            })
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read().decode("utf-8", "ignore")
            with open(path, "w", encoding="utf-8") as f:
                f.write(raw)
            time.sleep(sleep)
            return raw
        except Exception as e:  # noqa: BLE001
            last_err = e
            log("  重试 %d/%d %s (%s)" % (i + 1, retries, url, e))
            time.sleep(2 + 2 * i)
    if not force and os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    raise RuntimeError("抓取失败: %s (%s)" % (url, last_err))


def discover_links(max_pages=70, need_months=14):
    """从栏目列表页发现月度发布链接。列表页静态分页 index.html / index_N.html。"""
    found = {}
    for page in range(1, max_pages + 1):
        # 第 1 页是 index.html，第 2 页是 index_1.html，依次类推
        url = COLUMN if page == 1 else "%sindex_%d.html" % (COLUMN, page - 1)
        try:
            html = cached_get(url, sleep=0.6, force=True)
        except Exception as e:  # noqa: BLE001
            log("  列表页失败 %s: %s" % (url, e))
            continue
        for m in re.finditer(
                r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
            href, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
            text = re.sub(r"\s+", "", text)
            t = TITLE_RE.search(text)
            if not t:
                continue
            month = "%s-%02d" % (t.group(1), int(t.group(2)))
            if month in found:
                continue
            if href.startswith("./"):
                href = BASE + "/xxgk/sjfb/zxfb2020/" + href[2:]
            elif href.startswith("/"):
                href = BASE + href
            elif not href.startswith("http"):
                href = COLUMN + href
            found[month] = {"url": href, "title": text}
        log("  列表第 %d 页，累计发现 %d 个月" % (page, len(found)))
        if len(found) >= need_months and page >= 2:
            break
    return found


def norm_city(name):
    return re.sub(r"[\s\u3000]+", "", name)


def cell_text(c):
    return re.sub(r"[\s\u3000]+", "", c.get_text("", strip=True))


def guess_key(city):
    """城市唯一键（拼音不好维护，直接用中文，App 端按中文匹配）。"""
    return city


def table_kind(title_text):
    if "分类" in title_text:
        return None
    if "新建" in title_text:
        return "new"
    if "二手" in title_text:
        return "second"
    return None


def parse_article(html, url):
    soup = BeautifulSoup(html, "lxml")
    doc_title = ""
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    if m:
        doc_title = re.sub(r"\s+", "", m.group(1))

    # 发布日期：页面内“YYYY年M月D日”第一个有效期；再退回 URL 里的 tYYYYMMDD
    pub = None
    mm = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", soup.get_text(" ", strip=True)[:2000])
    if mm:
        pub = "%s-%02d-%02d" % (mm.group(1), int(mm.group(2)), int(mm.group(3)))
    if not pub:
        mm = re.search(r"t(20\d{2})(\d{2})(\d{2})_", url)
        if mm:
            pub = "%s-%s-%s" % (mm.group(1), mm.group(2), mm.group(3))

    tables = []
    for t in soup.find_all("table"):
        p = t.find_previous(lambda tag: tag.name == "p" and "销售价格" in tag.get_text())
        title = re.sub(r"\s+", "", p.get_text("", strip=True)) if p else ""
        tm = TABLE_TITLE_RE.search(title)
        if not tm:
            continue
        kind = table_kind(title)
        if not kind:
            continue
        tables.append((kind, title, tm, t))

    result = {"new": {}, "second": {}}
    for kind, title, tm, t in tables:
        rows = t.find_all("tr")
        for r in rows:
            cells = [cell_text(c) for c in r.find_all(["td", "th"])]
            # 8 列 = 左半页城市 + 右半页城市，各含 环比/同比/累计；
            # 1 月发布页没有“累计平均”列，只有 6 列。
            if len(cells) not in (6, 8):
                continue
            if cells[0] in ("城市", "上月=100", "上月＝100"):
                continue
            half = len(cells) // 2
            for group in (cells[:half], cells[half:]):
                city, mom, yoy = group[0], group[1], group[2]
                ytd = group[3] if len(group) > 3 else None
                if not city or not re.match(r"^[0-9.]+$", mom):
                    continue
                key = norm_city(city)
                # 同一城市重复出现（左/右半页）时以先出现为准；数据来自同一张表
                result[kind][key] = {
                    "city": key,
                    "mom_index": float(mom),
                    "yoy_index": float(yoy),
                    "ytd_index": float(ytd) if ytd and re.match(r"^[0-9.]+$", ytd) else None,
                }
        # 记录每张表的标题，便于报告里展示“按标题定位”的证据
        result.setdefault("_titles", []).append(title)

    missing = [k for k in ("new", "second") if not result[k]]
    if missing:
        raise ValueError("缺少表1/表2 数据: %s（%s）" % (missing, url))
    return pub, doc_title, result


def build_month_json(month, link, pub, doc_title, parsed):
    cities = []
    new_all, sec_all = parsed["new"], parsed["second"]
    if len(new_all) != 70 or len(sec_all) != 70:
        raise ValueError("%s 城市数异常：新房 %d，二手 %d" % (month, len(new_all), len(sec_all)))
    for city in sorted(new_all.keys(), key=lambda c: list(new_all).index(c)):
        n = new_all[city]
        s = sec_all.get(city)
        if not s:
            raise ValueError("%s 城市 %s 缺二手数据" % (month, city))
        def pct(v):
            return None if v is None else round(v - 100, 2)
        cities.append({
            "city": city,
            "new": {
                "mom_index": n["mom_index"], "mom_pct": pct(n["mom_index"]),
                "yoy_index": n["yoy_index"], "yoy_pct": pct(n["yoy_index"]),
                "ytd_index": n["ytd_index"], "ytd_pct": pct(n["ytd_index"]),
            },
            "second": {
                "mom_index": s["mom_index"], "mom_pct": pct(s["mom_index"]),
                "yoy_index": s["yoy_index"], "yoy_pct": pct(s["yoy_index"]),
                "ytd_index": s["ytd_index"], "ytd_pct": pct(s["ytd_index"]),
            },
        })
    y, mth = month.split("-")
    return {
        "schema_version": 1,
        "month": month,
        "month_label": "%s年%d月" % (y, int(mth)),
        "publish_date": pub,
        "source": {
            "title": doc_title or link["title"],
            "url": link["url"],
            "publisher": "国家统计局",
        },
        "caliber": {
            "new": "新建商品住宅销售价格指数（上月=100、上年同月=100、上年同期=100）",
            "second": "二手住宅销售价格指数（上月=100、上年同月=100、上年同期=100）",
            "survey_scope": "70 个大中城市市辖区，不含县；价格来自当地房地产管理部门网签数据。",
            "disclaimer": "指数是价格变动的相对值，不是均价；官方不发布 70 城住宅均价。",
        },
        "tables_located_by_title": parsed.get("_titles", []),
        "cities": cities,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=14)
    ap.add_argument("--month", type=str, default=None)
    ap.add_argument("--out", type=str, default=OUT_ANDROID,
                    help="输出目录（默认 app/src/main/assets/data；GitHub Actions 用 data）")
    ap.add_argument("--skip-existing", action="store_true",
                    help="该月 JSON 已存在则跳过抓取，避免重复抓同一期")
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out)
    os.makedirs(OUT_ROWS, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    links = discover_links(need_months=args.months)
    log("共发现 %d 个月度发布页" % len(links))
    selected = sorted(links.keys(), reverse=True)
    if args.month:
        selected = [m for m in selected if m == args.month]
    else:
        selected = selected[:args.months]

    ok, fail = 0, 0
    for month in selected:
        link = links[month]
        if args.skip_existing and os.path.exists(os.path.join(out_dir, "index_70cities_%s.json" % month)):
            ok += 1
            log("  == %s 已存在，跳过抓取（不重复发同一期）" % month)
            continue
        log("解析 %s  %s" % (month, link["url"]))
        try:
            html = cached_get(link["url"])
            pub, doc_title, parsed = parse_article(html, link["url"])
            data = build_month_json(month, link, pub, doc_title, parsed)
        except Exception as e:  # noqa: BLE001
            fail += 1
            log("  !! 失败: %s" % e)
            continue

        payload = json.dumps(data, ensure_ascii=False, indent=2)
        row_path = os.path.join(OUT_ROWS, "index_70cities_%s.json" % month)
        with open(row_path, "w", encoding="utf-8") as f:
            f.write(payload)
        with open(os.path.join(out_dir, "index_70cities_%s.json" % month), "w", encoding="utf-8") as f:
            f.write(payload)
        ok += 1
        log("  -> %s（%d 城，发布日 %s）" % (os.path.basename(row_path), len(data["cities"]), data["publish_date"]))

    # manifest 由 publish_data.py 统一生成（含 sha256 / data_version / 运行心跳）。
    # 这里不要再写 manifest：旧版会覆盖成精简版，丢掉 files 和 data_version，
    # 导致 Actions 里每次跑都产生一份「假变化」。
    log("完成：成功 %d 个月，失败 %d 个月" % (ok, fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
