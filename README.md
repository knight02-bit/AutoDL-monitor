# AutoDL 空闲 GPU 监控

实时盯 [AutoDL 创建页](https://www.autodl.com/create) 的 GPU 库存：**哪个地区、哪个型号、空了几张、多少钱一小时**。

有卡时弹 Windows 气泡 + 响铃；钉钉/飞书/企业微信/Bark 推送的代码已经写了，但还没接真实机器人联调。

```
 AutoDL 空闲 GPU 监控   16:02:53   第 7 次轮询 · 27s 后刷新
 账号 knight02 · Token 剩余 59 天 · 监控 RTX 4090, RTX 5090 · 9 个地区

地区          型号              空闲/总数    最低价  状态
─────────────────────────────────────────────────────────
北京B区       RTX 4090             1/1352   ¥2.29/h  有卡
西北B区       RTX 5090             2/2048   ¥2.93/h  有卡
北京B区       RTX 5090             1/2544   ¥3.03/h  有卡
西北B区       RTX 4090             0/1776         -  —

 共 3 个地区/型号有卡（RTX 4090, RTX 5090），空闲合计 4 张
 Ctrl+C 退出 · 日志 alerts.log · 打开创建页：https://www.autodl.com/create
```

## 完成情况

### ✅ 已完成并实测

| 能力 | 说明 |
| --- | --- |
| 库存拉取 | 9 个数据中心、33 个型号，每轮 9 个请求，口径和 /create 一致 |
| 地区 + 型号聚合 | 按 `data_center` 分组显示 `空闲/总数`；同一数据中心的多个分区已汇总 |
| 有卡提醒 | 0→有卡、空闲增加才提醒；同一地区+型号 10 分钟冷却，连续 3 轮只提醒 1 次 |
| Windows 气泡 | 系统通知已触发，PowerShell 调用返回成功（Win11 26200）；若没看到，检查专注助手 / 通知权限 |
| 告警日志 | 追加写 `alerts.log`，UTF-8 BOM，记事本打开不乱码 |
| 最低价 | 按量计费最低单价，按「地区+型号」缓存 6 小时 |
| 状态持久化 | 快照 / 冷却时间 / 价格存 `state.json`，重启不会重复提醒（已实测重载） |
| token 校验 | `--check` 显示账号、剩余天数；错误 token 会给出更换步骤（已实测） |
| 命令行 | `--once` `--json` `--all` `--models` `--demo` `--gpu` `--dc` `--min-idle` `--max-price` 均实测 |
| 中文对齐 | 全角字符按 2 列计算，表格不会错位（单测覆盖） |
| 单元测试 | 11 个用例全部通过 |

### 🟡 已实现，但没做真实联调

| 能力 | 现状 |
| --- | --- |
| Webhook 推送 | 钉钉 / 飞书 / 企微 / Bark / 通用 5 种请求格式已写好，**没有接真实机器人验证** |
| 自动打开创建页 | `--open` 已接 `webbrowser`，为避免测试时弹窗，没有实际跑过 |
| 提示音 | 已调用 `winsound.MessageBeep`，没有确认是否真的出声（可能被系统静音） |
| 非 Windows 运行 | 代码里有终端回退（响铃/无气泡），但只在 Windows 11 上验证过 |

### ⬜ 待完善（还没做）

- **自动下单 / 预约抢卡**：只提醒，不代替你创建实例（有意不做，避免误扣费）
- **图形界面**：目前只有终端看板 + 系统通知，没有网页面板或桌面 GUI
- **历史趋势**：只写 `alerts.log` 文本，不存时序数据，没有空闲数曲线
- **机器级监控**：只到「数据中心 + 型号」，看不到具体 `machine_id` 或主机标签
- **多账号**：不支持同时监控多个 token / 多个账号
- **token 自动续期**：过期只能手动重新 Copy as cURL
- **开机自启**：没有内置，需要自己加「任务计划程序」
- **推送失败重试**：失败只记 warning，不重试、不补发
- **代理配置**：只跟随系统 `HTTP(S)_PROXY`，没有单独的代理选项

## 设计取舍

- **只读、不下单**：查询和提醒自动化，创建实例始终由你在网页确认。
- **按数据中心聚合**：与 /create 的「选择地区」口径一致，不按单台机器统计。
- **只在变化时提醒**：配合冷却时间，长时间挂机也不会被刷屏。
- **价格缓存 6 小时**：价格变动不频繁，每轮只拉库存（9 个请求）。
- **纯 requests，无 GUI 依赖**：方便挂后台或塞进计划任务。

## 快速开始

```powershell
cd D:\project\AutoDL-monitor
pip install -r requirements.txt

# 1. 填 token（见下一节），确认登录态
python monitor.py --check

# 2. 开始监控
python monitor.py --gpu "RTX 4090,RTX 5090" --interval 20
```

也可以双击 `start.bat`。

## 获取 token

> 说明：AutoDL 的**开发者 Token**（控制台 → 设置 → 开发者Token）只能访问 `/api/v1/dev/*`（查余额等），**不能**查库存。库存接口要的是网页登录后的会话 token，大约 60 天有效。

1. 浏览器登录 <https://www.autodl.com>，打开 <https://www.autodl.com/create>
2. 按 `F12` → **Network** 面板 → 筛选 `api/v1`
3. 随便点一个请求（比如 `gpu_type`）→ 右键 → **Copy** → **Copy as cURL (bash)**
4. 在粘贴出来的命令里找这一行：

   ```
   -H 'authorization: eyJhbGciOi...'
   ```

   把 `eyJ` 开头的整串复制出来（不要 `Bearer` 前缀，不要引号）。
5. 填到 `.env`：

   ```
   AUTODL_TOKEN=eyJhbGciOi...
   ```

   或者临时用命令行：`python monitor.py --token "eyJ..."`

token 跟登录会话绑定，**换浏览器登录、点退出、改密码都可能失效**。工具会显示剩余天数，失效时会提示你重新获取。`--check` 可以随时验证。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `python monitor.py` | 按 `config.toml` 持续监控 |
| `python monitor.py --gpu "RTX 4090" --min-idle 2` | 只盯 4090，至少 2 张空闲才提醒 |
| `python monitor.py --dc westDC3,beijingDC1` | 只看西北B区、北京A区 |
| `python monitor.py --max-price 2.5` | 超过 2.5 元/小时的不提醒 |
| `python monitor.py --once` | 看一次当前库存就退出 |
| `python monitor.py --once --all` | 看全部型号（含 0 空闲） |
| `python monitor.py --json` | 输出 JSON（隐含 `--once`） |
| `python monitor.py --check` | 验证 token，打印账号、剩余天数、当前有卡型号 |
| `python monitor.py --models` | 列出所有型号名和空闲数，确定 `--gpu` 该填什么 |
| `python monitor.py --demo` | 离线演示表格 + 气泡 + 响铃 |
| `python monitor.py --open` | 有卡时自动打开创建页 |

`--gpu`、`--dc` 支持逗号分隔（中英文逗号都行），会覆盖配置文件里的列表。

型号名必须和接口完全一致（区分空格/大小写），常见的有 `RTX 4090`、`RTX 4090D`、`RTX 5090`、`RTX 5090 D`、`A100-PCIE-40GB`、`A800-80GB`、`H20-NVLink`。不确定就先跑 `python monitor.py --models` 看全量列表。

## 配置

所有选项都在 [config.toml](config.toml) 里，带中文注释。常用的几项：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `monitor.interval` | `30` | 轮询间隔（秒），最小 5；官方没公开限流，建议别低于 20 |
| `monitor.min_idle` | `1` | 至少几张空闲才算「有卡」 |
| `monitor.gpu` | `[]` | 只看这些型号；空 = 全部（表格只显示有卡的） |
| `monitor.data_center` | `[]` | 只看这些数据中心；空 = 全部 |
| `monitor.max_price` | `0` | 最高单价（元/小时），0 = 不限 |
| `monitor.alert_on_start` | `true` | 启动时 existing 的卡要不要提醒 |
| `notify.cooldown` | `600` | 同一地区+型号两次提醒的最小间隔（秒） |
| `notify.toast` / `notify.sound` | `true` | 气泡 / 提示音 |

地区代码（`data_center`）：`westDC3` 西北B区、`beijingDC1` 北京A区、`beijingDC2` 北京B区、`beijingDC3` V100专区、`chongqingDC1` 重庆A区、`neimengDC2` A800专区、`neimengDC3` 内蒙B区、`foshanDC1` 佛山区、`shanghaiDC1` 摩尔线程专区。

## 推送配置

填 `config.toml` 的 `[notify]` 段即可，可同时启用多个。**注意：这 5 种渠道只实现了请求格式，没有用真实机器人联调过**，第一次接入建议先让它发一条测试消息确认。

- **钉钉**：群机器人 Webhook → `dingtalk = "https://oapi.dingtalk.com/robot/send?access_token=..."`
- **飞书**：自定义机器人 → `feishu = "https://open.feishu.cn/open-apis/bot/v2/hook/..."`
- **企业微信**：群机器人 → `wecom = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."`
- **Bark（iOS）**：`bark = "https://api.day.app/你的key"`
- **通用**：任何接收 `{"text": "..."}` POST 的地址 → `webhook = "https://..."`

## 输出 JSON

```powershell
python monitor.py --json --gpu "RTX 4090" > gpu.json
```

> Windows PowerShell 5.1 的 `>` 重定向会写成 UTF-16。要 UTF-8 就用
> `python monitor.py --json | Out-File -Encoding utf8 gpu.json`，或直接升到 PowerShell 7。

```json
[
  {
    "data_center": "beijingDC2",
    "region_name": "北京B区",
    "gpu_name": "RTX 4090",
    "idle": 1,
    "total": 1352,
    "price": 2.29,
    "usage": 0.9993
  }
]
```

价格是「1 张卡按量计费」的最低单价（元/小时），拿不到时为 `null`。

## 工作原理

工具模拟创建页自身的请求，不是爬 HTML：

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/region/list` | 地区列表（公开） |
| `POST /api/v1/machine/region/gpu_type` | 按地区返回各型号 `idle_gpu_num / total_gpu_num`，每个数据中心一次请求，共 9 次/轮 |
| `POST /api/v1/instance/schedule/order/price/preview` | 某地区某型号的最低价，按 `地区+型号` 缓存 6 小时 |
| `GET /api/v1/user/detail` | 验证 token、显示账号 |

请求头带 `authorization`（网页登录 JWT）和 `appversion`。前端 JS 里没有额外的签名参数。

## 常见问题

**提示「登录态失效 / 登录超时」**
token 过期或被登出。按上面步骤重新 Copy as cURL，更新 `.env`，`python monitor.py --check` 验证。

**气泡不弹**
Windows 专注助手/勿扰模式会拦截；公司电脑可能禁用通知。`--demo` 可单独测试。表格和 `alerts.log` 不受影响。

**数字和网页对不上**
网页 /create 上的数字是「选中地区」的汇总；本工具按数据中心分组，口径相同。库存每秒都在变，差几秒很正常。

**会被封号吗**
用的是你本人登录态的只读接口，频率默认 30 秒一次、9 个请求，属于正常使用量级。不建议把间隔调到 5 秒以下。

**token 会不会泄露**
只存在本地 `.env`（已在 `.gitignore` 里），只发给 `www.autodl.com`。不要把它贴到公开仓库或聊天群。

**能自动下单 / 帮我抢卡吗**
不能，这是有意设计的：只提醒，创建实例仍由你在网页确认。自动下单要处理库存竞争、计费方式、退订规则，风险远大于收益（详见[完成情况](#完成情况)里的「待完善」）。

**为什么只有终端界面**
当前版本优先保证「能稳定拉到数据、能可靠提醒」。GUI / Web 面板、历史曲线、机器级监控都在待完善清单里，还没做。

## 目录

```
monitor.py                 入口 / 命令行
autodl_monitor/api.py      接口封装
autodl_monitor/models.py   快照、过滤、告警判定
autodl_monitor/config.py   配置加载
autodl_monitor/runner.py   轮询主循环
autodl_monitor/render.py   表格渲染（中文对齐）
autodl_monitor/notify.py   气泡 / 声音 / Webhook / 日志
autodl_monitor/state.py    状态持久化
config.toml                配置
.env                       token（不要提交）
alerts.log                 告警历史
```

> 本工具只做查询和提醒，不会自动下单。创建实例仍需你自己在网页确认，避免误扣费。
