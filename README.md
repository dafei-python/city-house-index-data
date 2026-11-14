# 房价趋势（CityHouseIndex）

一个查看中国 70 个大中城市房价指数与官方成交量的 Android 应用。数据全部来自公开官方发布：国家统计局月度价格指数、各市住建部门网签数据、省级年度商品房数据。App 离线可用，联网时按月/按日增量更新。

给两类人用：想按官方口径看房价走势、又不想被二手挂牌价和中介话术干扰的人；以及想知道某个城市最近一个月到底成交了多少套的人。

## 主要功能

| 页面 | 做什么 |
| --- | --- |
| 70 城指数（首页） | 70 城新房 / 二手房指数排行，看同比、环比和最近 12 个月走势；支持按城市名搜索 |
| 省份年度均价 | 31 个省级单位的年度商品房销售均价，可下钻到省内城市 |
| 成交量 | 官方网签口径的成交，区分单日与累计、套数与面积；6 城日更 + 2 城月报 |
| 城市详情 | 12 个月指数折线、推算参考价、基准价出处与官方原文链接 |
| 数据说明 | 口径、推算方法、误差来源、每个城市用的是哪种基准 |
| 刷新更新 | 检查数据版本，按文件 sha256 增量下载，失败自动回退到内置数据 |

## 数据从哪来

- **70 城指数**：国家统计局《70 个大中城市商品住宅销售价格变动情况》，新建商品住宅 / 二手住宅销售价格指数，月更。
- **成交量**：各市住建部门 / 住房保障和房产管理局的网签公示。北京、青岛、苏州、南京、杭州、广州为日更；西安、武汉官方只发整月累计月报，按月更新。
- **省级年度均价**：各省统计公报、统计年鉴中的商品房销售面积与销售额，年更。

频率与滞后：指数每月 15–19 日前后发布上月数据，所以 App 里的「最新一期」通常比当前月份晚一个月；网签一般是 T+1；省级年度数据次年才齐。

指数是「同比 / 环比」的相对值，不含绝对价格。App 里的参考价是「可核实的官方年度基准价 × 指数变化」推算出来的，不是成交均价。

## 技术栈

- **语言 / UI**：Kotlin 2.2.10，Jetpack Compose（Material 3）
- **构建**：AGP 8.13.2，Gradle 8.14.5，JDK 17，compileSdk / targetSdk 36，minSdk 26
- **图表**：Compose Canvas 手绘折线与柱状，不依赖第三方图表库
- **数据存储**：JSON。内置在 `assets/data/`，更新后的版本写到 `filesDir/dataset`，按 `data_version` 取较新的一份
- **更新机制**：多个 jsDelivr 镜像线路按顺序降级（主线路失效自动切换）；`manifest.json` 登记每个文件的 sha256，逐个比对只下变更文件，校验不过就放弃本次更新

## 项目结构

```
fangjia/
├── app/                             Android 应用
│   ├── src/main/java/com/cityhouse/index/   页面、图表、数据解析、更新逻辑
│   ├── src/main/assets/data/        内置数据（指数 / 基准价 / 网签 / 省份 / manifest）
│   ├── src/main/res/                图标、颜色、主题（含 Android 13 主题图标）
│   └── build.gradle.kts
├── data/                            线上数据目录，GitHub Actions 直接提交到这里
├── data-pipeline/                   抓取与校验脚本（Python）
│   ├── scrape_70cities.py           抓国家统计局月度指数
│   ├── scrape_deals.py              抓各市网签成交量
│   ├── build_extra_data.py          基准价、省级年度数据
│   ├── publish_data.py              重建 manifest（data_version + sha256）
│   ├── should_update_index.py       判断今天是否值得抓
│   └── verify_and_report.py         抽查数据
├── .github/workflows/update-index.yml 房价指数定时更新（月更）
├── .github/workflows/update-deals.yml 成交量定时更新（日更，6 城）
├── .github/workflows/update-deals-monthly.yml 西安/武汉月报（每月 3–10 日）
├── keystore/                        发布签名（cityhouse-release.jks，不对外）
├── release/                         发布包 CityHouseIndex-1.3.7-release.apk（1.3.5/1.3.6 保留）
├── gradle/ gradlew gradlew.bat      Gradle Wrapper
├── build.gradle.kts settings.gradle.kts gradle.properties
└── README.md
```

## 如何构建

需要 JDK 17+、Android SDK Platform 36。

```bash
# Android Studio 打开一次会自动生成 local.properties；命令行构建也可以直接给环境变量
export ANDROID_HOME="$HOME/Library/Android/sdk"

./gradlew assembleRelease
```

产物：`app/build/outputs/apk/release/app-release.apk`。

签名：`app/build.gradle.kts` 从 `gradle.properties` 读四个键（`RELEASE_STORE_FILE`、`RELEASE_STORE_PASSWORD`、`RELEASE_KEY_ALIAS`、`RELEASE_KEY_PASSWORD`），证书在 `keystore/cityhouse-release.jks`。这个 keystore 是覆盖升级的凭据，换一份签名，老用户就装不上新包了，所以不要丢、也不要提交到公开仓库。

`local.properties` 里是本机 SDK 路径，不纳入版本管理；删掉不影响构建，只要有 `ANDROID_HOME`。

## 数据更新

数据目录 `data/` 与 App 内置的 `assets/data/` 是同一套结构，`manifest.json` 记录 `data_version`（YYYYMMDDHHMMSS，UTC）和每个文件的 sha256。

`data/` 下三个独立 workflow（各自 schedule，互不触发对方）：

- `update-index.yml`（房价指数，月更）：北京时间 10:40 主跑、14:40 补跑。
  只有落在 15–19 日窗口、且本地还没有新月份时才抓取；1–14 日只记一次「窗口外」心跳，不下载。
- `update-deals.yml`（成交量，日更）：北京时间 08:20 主跑、13:20 补跑、22:40 晚班。
  只抓 6 个真有日更页面的城市；全部城市抓取失败时脚本失败退出、保留仓库里的旧数据。
  晚班是为了让苏州/南京/杭州/青岛这类「当日累计」城市在当晚接近日终值时再刷新一次。
- `update-deals-monthly.yml`（西安/武汉，月更）：每月 3–10 日 09:20 跑一次，只替换这两城的月报条目，
  不动日更城市的数据。

cron 一律按 UTC 写：`40 2 * * *` = 10:40、`20 0 * * *` = 08:20、`20 5 * * *` = 13:20、`40 14 * * *` = 22:40（均为 UTC+8）。
补跑与晚班是为了兜住 GitHub 调度器的偶发延迟/漏跑——它是 best-effort，不保证准点。

### 成交量各城的时间口径（实测）

| 城市 | 页面给的是 | 什么时候抓最完整 |
| --- | --- | --- |
| 北京 | 前一日的期房/现房/存量房日结 | 次日任意时刻（页面显示的是前一日的完整值） |
| 广州 | 前一日的分区签约汇总（`createTime` 为前一日） | 次日任意时刻，越晚越保险 |
| 苏州 / 南京 / 杭州 / 青岛 | **当日累计**，午夜清零，不提供历史 | 当日越晚越完整（22:40 晚班接近日终值） |
| 西安 | 上月二手房网签备案（次月初发布） | 月报发布后（约次月 5–6 日）；**GitHub runner 取不到，见下** |
| 武汉 | 上月新建商品房成交（次月初发布） | 月报发布后（约次月 3–5 日） |

**西安站点对境外/云 IP 返回 403**（实拍证据：GitHub runner 上 `http=403`，返回的是拦截页而非正文）。
所以西安月报在 Actions 里抓不到，任务会把它记为失败、并保留仓库里上一份月报数据（不会被清空）。
新月份发布后，需要在**国内网络环境**本地跑一次：

```bash
python3 data-pipeline/scrape_deals.py --monthly
python3 data-pipeline/publish_data.py
```

然后把 `data/` 与 `assets/data/` 的改动提交上去。

所以「提前到 07:20」对北京/广州没有实质帮助（它们本来就是 T+1），对苏州/南京/杭州/青岛反而更少——
这几城 07:20 拿到的是新一天的个位数。想要「早上就显示昨日最终值」，必须保留昨日快照，
当前 `deals.json` 只存最新一份，属于后续结构改动。

### 怎么判断「没跑」还是「跑了没新数」

`data/manifest.json` 里除了 `data_version`，还记录：

| 字段 | 含义 |
| --- | --- |
| `last_run_at` / `last_run_kind` | 最近一次运行的时间（UTC）和是哪个 workflow |
| `last_index_run_at` / `last_index_result` | 指数 workflow 最近运行时间 / 结果（`changed`、`no_change`、`not_in_window`） |
| `last_deals_run_at` / `last_deals_result` | 成交量 workflow 最近运行时间 / 结果（`changed`、`no_change`） |
| `deals_latest` | 成交量数据实际到哪一天（如 `2026-11-08`） |
| `latest_month` | 房价指数最新月份（如 `2026-09`） |

同一个 workflow 每天只写一次心跳：当天的补跑/重试不会再产生提交。数据真的变了才写 `data(...)` 提交，只更新心跳时提交信息是 `chore(data): ... 运行心跳 ...`。

仓库需要开启 Actions，并给 workflow `contents: write` 权限（文件里已声明）。手动跑可以在 Actions 页面点 Run workflow。

> 注意：仓库 60 天无提交活动时，GitHub 会自动停用定时任务；fork 出来的仓库默认不启用 workflow，都需要手动点 Enable。

App 侧的数据源在 `app/src/main/java/com/cityhouse/index/UpdateConfig.kt`，按可用性顺序多线路降级：

```kotlin
val SOURCES = listOf(
    Source("b-cdn",   "https://jsdelivr.b-cdn.net/gh/dafei-python/city-house-index-data@main/data/"),
    Source("fastly",  "https://fastly.jsdelivr.net/gh/dafei-python/city-house-index-data@main/data/"),
    Source("gcore",   "https://gcore.jsdelivr.net/gh/dafei-python/city-house-index-data@main/data/"),
    Source("jsdelivr","https://cdn.jsdelivr.net/gh/dafei-python/city-house-index-data@main/data/"),
    Source("github-raw", "https://raw.githubusercontent.com/dafei-python/city-house-index-data/main/data/"),
)
```

为什么要多线路：国内部分网络对 `cdn.jsdelivr.net` 与 `raw.githubusercontent.com` 会做 TLS 拦截/证书替换，App 侧表现为「证书校验失败」。
2026-11-11 实测（同一网络、同一时刻，请求 `manifest.json`）：

| 线路 | HTTP | 耗时 | 证书 | 结论 |
| --- | --- | --- | --- | --- |
| `jsdelivr.b-cdn.net` | 200 | 1.3s | `CN=*.b-cdn.net`，SSL.com TLS Issuing RSA CA R1 | 可用 |
| `fastly.jsdelivr.net` | 200 | 1.7s | `CN=jsdelivr.net`，GlobalSign Atlas R46 DV TLS CA 2026 Q4 | 可用 |
| `gcore.jsdelivr.net` | 200 | 8.6s | 同上（Cloudflare 前置） | 可用但慢 |
| `cdn.jsdelivr.net` | 000 | — | 握手失败，无法取证书 | **不可用** |
| `raw.githubusercontent.com` | 000 | — | 握手失败，无法取证书 | **不可用** |
| `dafei-python.github.io` | 404 | 0.8s | 正常 | 未开 Pages |

换成自己的仓库时改 `SOURCES` 里的仓库名即可；地址里保留 `REPLACE_WITH_YOUR_GITHUB` 占位串时，App 不发请求，只用内置数据。
刷新失败时界面只显示一句话（连不上更新服务器 / 安全连接被拦截 / 暂时没有数据 / 未配置），技术明细折叠在「查看详情」里。

## 已知边界

- **成交量覆盖 6 城日更 + 2 城月报**：其余城市没有公开、稳定、可日更的网签接口。
- **西安、武汉只有月报**：西安 10 月二手房网签 96.11 万㎡（住宅 8506 套），官方栏目只发按月通报，没有日更页面，所以按「月更」标注口径，不进日更任务。西安站点另对境外/云 IP 返回 403，Actions 抓不到，需国内网络本地更新后推送。
- **基准价只保留可核验锚点，共 13 城**：
  - 11 城为机构样本锚点（中指院百城新建住宅，城区在售报价），`confidence=verified_institution`；
  - 2 城为官方成交均价（长沙 10,954 元/㎡、沈阳 10,257 元/㎡），`confidence=verified_official`；
  - 其余 57 城只显示指数、不显示金额。
- **年鉴法已全面停用（2026-11）**：抽验 6 城，中位偏差 **+39%**（中位 3,308 元/㎡，最差 23,902 元/㎡）；新增抽验青岛 +3,031、长沙 +1,429、大连 +2,100、昆明 +3,390，均超 1,000 元门槛。32 个原年鉴锚点移出展示，原值保存在 `base_prices.json` 的 `excluded`，可随时恢复。
- **远端不会覆盖价格**：`base_prices.json` 与 `provinces.json` 随 APK 内置，更新时只下载指数与成交量，避免旧数据回灌。
- **没有「估算」值**：不做省级、邻近城市或指数反推的虚构锚点；无法核实的城市宁可只显示指数。
- **深圳**的按日成交数据集需要申请 appKey，本项目未获授权，未接入。
- **成都**官网是单页应用，没有公开的 GET 接口；**合肥**没有全市日更的公开网签数据。这两城只标注「未接入」，不做猜测。
- **二手房**：官方不发布 70 城二手住宅均价，也没有可核实的二手基准价，因此二手页面只给指数，不推算金额。

## 免责声明

数据来自公开官方渠道，仅作参考，不构成任何交易建议。推算参考价 = 年度锚点 ×（1 + 最新同比指数）：13 城锚点为机构样本价或官方成交均价，其余 57 城不显示金额；误差来源与实测分布见 App 内「数据说明」页及 `data-pipeline/backtest/`。

## 价格展示规则（1.4.0）

数据源：中指研究院《百城价格指数》2026 年 10 月（免费公开页，新房 / 二手各 100 城）。

| 精度 | 条件 | 展示 |
|---|---|---|
| 元级 | 样本价 ≤8,000 元/㎡（15% 误差 ≤1,200 元） | `约 5,110 元/㎡` |
| 万级 | 样本价 >8,000 元/㎡ | `约 6.6 万/㎡` |
| 精确 | 已用官方成交均价核验且误差 ≤1,000 元（沈阳、长沙） | `10,308 元/㎡` |
| 仅指数 | 不在百城样本内，且无官方均价 | 不显示价格 |

覆盖：70 城中 54 城有新房样本价（长沙、沈阳改用户官方精确值），54 城有二手房挂牌样本价；16 城（丹东、锦州、吉林、牡丹江、蚌埠、安庆、九江、平顶山、襄阳、岳阳、常德、韶关、泸州、南充、遵义、大理）不在百城样本内，官方也不发布城市均价，只显示指数。

实测误差（新房，对照官方/成交均价）：沈阳 5.3%、大连 12.0%、长沙 12.9%、青岛 13.4%，中位约 12.5%；高价城市绝对误差可达数千元，故不显示精确值。二手房为挂牌口径，成交通常再低 5%–10%。
