<div align="center">

<img src="docs/assets/logo.svg" width="88" height="88" alt="ScrcpyGate logo">

# ScrcpyGate

**自托管的浏览器投屏与远程控制网关。**

scrcpy server · 原生 WebSocket · Raw v2 分帧 · FastAPI · 前端无需打包

[![Builds](https://github.com/Ange-Katrina/ScrcpyGate/actions/workflows/Builds.yml/badge.svg?branch=main)](https://github.com/Ange-Katrina/ScrcpyGate/actions/workflows/Builds.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-3776AB.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.139-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/docker-amd64%20%7C%20arm64-2496ED.svg)](https://github.com/Ange-Katrina/ScrcpyGate/pkgs/container/scrcpygate)
[![Third party](https://img.shields.io/badge/third--party-THIRD__PARTY.md-lightgrey.svg)](THIRD_PARTY.md)

[English](README.md) · 简体中文

[贡献指南](CONTRIBUTING.md) · [漏洞报告](SECURITY.md)

</div>

---

ScrcpyGate 通过 ADB 驱动官方 **scrcpy server**，把原始 H.264 码流用**原生 WebSocket** 转发到
浏览器，并补齐共享部署真正需要的东西：账号与角色、设备授权、显式控制租约、防篡改审计日志、
画质档位，以及可选的 **ALAS** 集成。

> 它不是把 ADB 直接暴露到网络上的薄壳页面，而是给反向代理 / WAF 后面使用的小型管理网关：
> *谁能看、谁能控、发生了什么* 都是内建能力。

## 安装

```sh
git clone https://github.com/Ange-Katrina/ScrcpyGate.git
cd ScrcpyGate
./deploy.sh --install
```

跑完打开 `http://127.0.0.1:5000`，用 `admin` 登录。

**推荐路径**：安装脚本是受支持的入口 —— 它替你完成配置、构建、启动与健康检查（细节见
[快速开始](#快速开始)）。下面那些是给「想看、想自己驱动每个部件」的人准备的分层入口。

| 路径 | 适用 | 起步命令 |
| --- | --- | --- |
| **[deploy.sh](#推荐-deploysh)** | 首次安装，以及希望被引导的主机 | `./deploy.sh --install` |
| [Docker](#docker-用户-compose) | 你想自己管理 Compose 栈 | `docker compose up -d --build` |
| [宿主网络 / USB](#特殊场景-宿主网络与-usb) | 复用宿主 adb server、USB 设备、特殊网络 | `docker compose -f compose.host.yaml up -d --build` |
| [开发](#开发-uvicorn) | 从源码直接跑 | `uvicorn app.main:app` |

无论走哪条，跑的都是同一个镜像里的同一个 `app.main:app`。

## 目录

- [核心特性](#核心特性)
- [工作原理](#工作原理)
- [快速开始](#快速开始)
  - [推荐: deploy.sh](#推荐-deploysh)
  - [Docker 用户: Compose](#docker-用户-compose)
  - [特殊场景: 宿主网络与 USB](#特殊场景-宿主网络与-usb)
  - [开发: uvicorn](#开发-uvicorn)
- [运维](#运维)
- [高级与故障恢复](#高级与故障恢复)
- [配置](#配置)
- [安全模型](#安全模型)

## 核心特性

| | |
| --- | --- |
| **实时投屏与控制** | 面向网络 ADB 设备，单设备多观看端，同一时刻一个控制者 —— 控制权是带过期的显式租约，管理员强制接管会留审计记录。 |
| **Raw v2 流式通道** | scrcpy server → 每观看端独立有界队列 → WebSocket → 浏览器；关键帧感知恢复、慢观看端隔离、自适应画框，可选低延迟编码器提示。 |
| **跟随设备转屏** | 设备屏幕方向变化时重建采集并重新预备观看端；浏览器保持画面方向正确，全屏时还会请求系统真的转屏。 |
| **账号、角色与权限** | 按用户授权设备，观看与控制分离，会话与到期处理，按角色配置工作台。 |
| **可编排的工作台** | 管理员按角色决定：控制栏按钮、状态条功能块、一级/二级菜单布局、全屏行为开关。 |
| **审计与通知** | 带哈希链的审计事件、告警与保留策略，应用内通知与观看历史。 |
| **ALAS 集成** | 可选：嵌入式 ALAS 界面（带可见性策略边界）、出站网关（主机/CIDR 白名单）、加密令牌 —— 密钥文件放数据目录，**绝不写进 `.env`**。 |
| **移动优先的前端** | 无打包器的多页静态前端：宫格视图、全屏投屏、边缘把手、触摸映射、中英 i18n。 |
| **Docker 优先** | 多架构镜像（amd64 + arm64）、非 root、健康检查，compose 样例带 `cap_drop: ALL` 与 `no-new-privileges`。 |
| **发布链路** | 镜像出厂前先试跑并扫描，推 GHCR 时带 provenance/SBOM，支持按 digest 部署并自动回滚。每周还会盯着上游 scrcpy：它发了新版就先出一个**未验证的 canary 镜像**，验证通过前 `latest` 不动。 |

## 工作原理

```mermaid
flowchart LR
  B["浏览器<br/>投屏 · 宫格 · 管理台"]
  A["FastAPI 应用<br/>app.main:app"]
  D["Android 设备<br/>scrcpy server v3.1"]
  B -- "HTTP" --> A
  A -- "WebSocket · Raw v2" --> B
  A -- "ADB" --> D
  D -- "H.264 · 设备元信息 · 控制" --> A
```

| 路径 | 内容 |
| --- | --- |
| `app/routers/` | HTTP 与 WebSocket 接口 —— 93 条路由：鉴权、设备、投屏、ALAS、管理、审计 |
| `app/mirror_manager.py`、`app/mirror_runtime.py`、`app/mirror_websocket.py` | 会话生命周期、码流分发、每观看端队列、控制租约 |
| `app/scrcpy_demuxer.py`、`app/h264.py` | Raw v2 分帧、NAL / 关键帧处理 |
| `app/alas_*` | ALAS 嵌入传输、策略、网关、密钥、可见性 |
| `app/security.py`、`app/login_guard.py`、`app/audit_*` | 会话、CSRF / Origin 规则、登录防护、审计链 |
| `static/` | 无打包器的前端（页面 + 共享模块 + CSS） |
| `compose.yaml`、`compose.host.yaml` | 两种受支持的部署形态 |
| `docs/` | 高级部署配方（`docker run`）与 README 头图 |
| `tools/` | 部署辅助脚本（不进镜像） |

## 快速开始

**环境要求** —— Linux 主机 + Docker 24 与 Compose v2（安装脚本可以用 `--install-deps` 帮你装），
以及打开 USB 调试、能通过 ADB 访问的 Android 设备。

### 推荐: deploy.sh

```sh
git clone https://github.com/Ange-Katrina/ScrcpyGate.git
cd ScrcpyGate
./deploy.sh --install
```

一条命令，按顺序把首次安装全部做完：

1. 从 `.env.example` 生成 `.env`（或用 `--configure` 进入交互式向导）；
2. 校验配置，并检查端口/容器冲突；
3. 创建数据目录；
4. 构建镜像，再复用 ALAS 令牌加密密钥，或为新数据生成密钥；
5. 修正数据目录属主，使其匹配容器用户（`uid 100` / `gid 101`）；
6. 创建管理员账号并打印密码；
7. 启动整套服务，并等待 `/healthz` 健康门通过。

结束时会打印要访问的地址与管理员密码。以后重复执行同一条命令就是升级；已有 `.env` 的值、
数据库与密码都不会被覆盖。

不带参数运行 `./deploy.sh` 会打开交互式菜单，功能与命令行一致 —— 安装/更新、启停重启、状态、
日志、配置、重置管理员、备份、ALAS 令牌状态与迁移、环境检查与卸载。

保留账号和配置重装：先执行 `./deploy.sh --uninstall`，再执行 `./deploy.sh --install`。
普通卸载始终保留 `.env`、本地镜像、数据库及其 ALAS 密钥，重装沿用原密码。
管理菜单中的“更新到已发布镜像”会自动查询 GHCR，按编号选择实际存在的
`latest`（稳定版）、`edge`（开发版）或最近的正式版本，确认完整镜像地址后才开始更新。
查询需要宿主 Python 3；查询失败时仍可使用配置中的默认目标，或手动输入完整 tag/digest 引用。

菜单中的“卸载 ScrcpyGate”可选择**保留数据卸载**（默认）或**彻底清理**；
如果原密码未保存，请使用 `./deploy.sh --reset-admin`。

需要清除本地数据后全新安装时，才使用 `./deploy.sh --uninstall --purge`。
清理中断或失败会保留 `.env`，可修复原因后重复执行同一命令。项目目录外的数据不会自动删除，
对应配置也会保留，并报告清理未完成。备份不会被删除；迁移部署时须一起保留数据库和匹配的密钥。
交互式彻底清理会要求输入完整数据路径，确认后才开始删除；留空取消，`--yes` 不会跳过确认。
非交互的 `--uninstall --purge` 是明确的破坏性命令，不会再次询问。
如果旧版卸载已删除容器和 `.env`，彻底清理仍可识别默认 `./data` 中带 SQLite 文件头的
`webscrcpy.db` 并清理。无法识别的目录和符号链接会保留；请恢复原 `.env`、核对数据路径后重试。
清理已识别的旧版数据前，会先保存仅含数据路径的最小 `.env`；即使中途已删除数据库，也可再次执行清理。
彻底清理会删除账号、ALAS 凭据和已持久化的 ADB 授权，重装需重新配置。
如果仅丢失 ALAS 密钥，优先使用下方的“仅重置 ALAS Token”恢复方式。
如果旧数据库含加密 ALAS Token 而密钥缺失，脚本会优先提示恢复原密钥。
交互安装会询问是否强行重置 ALAS Token 并继续安装，默认选择“不重置”；确认后只清空 ALAS Token、
生成新密钥，账号、密码、设备和其他配置保留。非交互安装不会自动接受重置，`--yes` 也不会跳过此确认。
确实无法找回时，可明确执行 `./deploy.sh --clear-alas-token`，再重装并重新填写 ALAS Token；
这只清空保存的 ALAS 凭据，不删除账号。已有但无效的密钥文件仍须单独修复，重装不会擅自覆盖。
安装源码修复时不要使用 `--skip-build`。

### Docker 用户: Compose

如果你更想自己驱动整个栈，安装脚本做的其实就是下面这些 —— 只是这些部件都由你自己管：

```sh
git clone https://github.com/Ange-Katrina/ScrcpyGate.git
cd ScrcpyGate
cp .env.example .env

# 在 .env 里设置首启管理员密码（长度至少 MIN_PASSWORD_LENGTH＝12）：
#   INITIAL_ADMIN_PASSWORD=<至少 12 位的强密码>

# 容器以 uid 100(app):gid 101(app) 运行，宿主数据目录必须可被它写入，
# 否则启动即 PermissionError: /app/data/.init-db.lock 并反复重启。
mkdir -p ./data && sudo chown -R 100:101 ./data

docker compose up -d --build
```

有两件事安装脚本替你做了、而 Compose 不会：上面的数据目录属主，以及创建管理员账号。如果没设密码，
应用会把生成的密码写进 `./data/initial_admin_password.txt`（权限 `0600`，下次启动即删除），
不会再把你锁在外面 —— 见[高级与故障恢复](#高级与故障恢复)。

容器入口会为新数据或尚未加密的数据配置 ALAS 密钥，复用已有密钥。
数据库含加密 Token 却缺失密钥时，不生成替代密钥；请恢复匹配密钥，核心服务仍可启动并显示 ALAS 告警。
在 `.env` 中设置 `SCRCPYGATE_ADMIN_PASSWORD_FILE=false` 可关闭首次密码落盘。

容器内 ADB 的授权身份现在保存在 `data/.android`。替换使用旧 `/tmp` 路径的容器前，
请先按 [ADB 迁移步骤](docs/deployment/docker-run.zh.md#5-生命周期与升级) 保留原身份。

### 特殊场景: 宿主网络与 USB

需要容器与宿主共用网络栈时用 `compose.host.yaml` —— 典型场景是复用宿主已有的 adb server
（宿主 `adb devices` 已能看到 USB 直连的手机），或访问只在宿主 loopback 上监听的服务：

```sh
docker compose -f compose.host.yaml up -d --build
```

它从 `compose.yaml` 继承整份服务定义，所以环境变量只有一处要维护。这个模式下没有端口映射：
绑定位置由 `WEB_SCRCPY_BIND` / `WEB_SCRCPY_PORT` 决定，记得与 `PUBLIC_BASE_URL` 保持一致。

要让容器使用宿主的 adb server，在 `.env` 里设 `ADB_SERVER_SOCKET=tcp:127.0.0.1:5037`；
反过来，想把 USB 设备直接交给容器则需要 `--device /dev/bus/usb --privileged`。

### 开发: uvicorn

```sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export WEB_SCRCPY_DATA_DIR=./data PUBLIC_BASE_URL=http://127.0.0.1:5000 \
       ALLOWED_HOSTS=127.0.0.1,localhost SESSION_COOKIE_SECURE=false
uvicorn app.main:app --host 127.0.0.1 --port 5000
```

> [!IMPORTANT]
> 应用**从不读取 `.env`**（没有 dotenv 装载），只读**进程环境变量**。`.env` 是给
> `docker compose`（`${VAR}` 插值）和 `deploy.sh`（按名取值）用的。直接跑 `uvicorn` 必须自己导出。

## 运维

CI/CD 验证 amd64 和 arm64 镜像后发布到 GHCR，服务器手动部署。
版本标签、包权限、镜像证明和部署步骤见 [CI/CD 与手动部署](docs/deployment/ci-cd.zh.md)。

产品版本统一定义在 [`VERSION`](VERSION)，版本递增、开发镜像标识与显示规则见
[版本号规范](docs/contributing/versioning.zh.md)。

健康检查：`GET /healthz`；日志：stdout（JSON，`LOG_FORMAT=json`），可选写入数据目录。

画面出问题（"黑一下""卡住""自己停了"）时，管理员可以在工作台侧边栏打开**投屏记录**：按时间
列出连接状态、关键帧等待、序列跳号、解码器重建、延迟回跳、服务端重置、通道关闭码、码率与
分辨率变化、控制权变更等事件，可一键复制或导出 JSON。选择**仅本端记录**时，时间线保留在
当前标签页内存里；选择**多端记录**时，邀请正在观看同一设备的其他客户端，对方可参与或拒绝。
管理员停止并汇总时，已同意的客户端上传时间线，服务端通过内存转发给发起端浏览器。
汇总导出按客户端分段，不混排不同机器的时钟。中继不持久化时间线内容，操作元数据另有审计；
服务端事件看 `/logs`。

每个浏览器保留最近 **800 条事件**，满后移除最早事件。服务端保留已接受上传的字段及未知键，
但不能恢复浏览器已淘汰的事件。默认会话有效期为 15 分钟，停止后上传窗口为 20 秒，
最多邀请 12 个参与端，时间线限制为 20000 条、2 MiB。整个 JSON 请求还受
`API_REQUEST_BODY_MAX_BYTES` 限制（默认 1 MiB）；需要接受更大时间线时也须调整这个上限，
并为会话号、客户端号等请求封装预留空间。可在 Compose `.env` 中配置
`MIRROR_RECORD_TTL_SECONDS`、`MIRROR_RECORD_UPLOAD_GRACE_SECONDS`、`MIRROR_RECORD_UPLOAD_MAX_BYTES`、
`MIRROR_RECORD_MAX_ENTRIES`、`MIRROR_RECORD_MAX_PARTICIPANTS`，两种网络模式都会继承；
直接运行 Python 时则导出对应环境变量。

用安装脚本（推荐 —— 它包的是同一个 Compose 项目）：

| 命令 | 作用 |
| --- | --- |
| `./deploy.sh --install` | 安装或更新，带健康门 |
| `./deploy.sh --configure` / `--menu` | 交互式 `.env` 向导 / 管理菜单 |
| `./deploy.sh --install-deps` / `--pull` / `--skip-build` | 安装 Docker + Compose、刷新基础镜像、复用已有的本地镜像 |
| `./deploy.sh --start` / `--stop` / `--restart` / `--status` | 生命周期与状态 |
| `./deploy.sh --logs [行数]` | 日志尾部 |
| `./deploy.sh --check` / `--check-conflicts` / `--check-production` | 只读自检、端口/容器冲突检查、生产边界校验 |
| `./deploy.sh --backup [路径] [--keep N]` / `--list-backups` | 备份数据目录（在线 SQLite 快照 + ALAS 密钥 + 清单 + 校验和，`--keep N` 只留最近 N 份）/ 列出归档 |
| `./deploy.sh --restore <归档> [--yes] [--no-restart] [--data-only]` | 恢复备份：先把当前数据另存为归档，重启并过健康门，失败自动回滚 |
| `./deploy.sh --candidate-manifest [文件]` | 干净 checkout 的文件哈希与镜像 digest |
| `./deploy.sh --uninstall [--purge]` | 移除整套服务（带 `--purge` 连数据目录一起删） |

用 Compose 安装、并且想继续留在 Compose 里，就用这些：

```sh
docker compose ps
docker compose logs -f
docker compose up -d                            # 应用配置变更
docker compose pull && docker compose up -d     # 升级镜像
docker compose down
```

带门的发布 —— 不可变 digest + 健康检查 + 失败自动回滚：

```sh
sh tools/deploy_release.sh \
  --image ghcr.io/<owner>/scrcpygate@sha256:<digest> \
  --compose-dir /path/to/deployment \
  --health-url http://127.0.0.1:5000/healthz
# 退出码：0 已部署 · 2 已回滚到上一版 · 3 回滚后仍不健康 · 1 参数/环境错误
```

## 高级与故障恢复

**忘记管理员密码。** 创建管理员账号时如果没设密码，生成的密码会被写进
`data/initial_admin_password.txt`（权限 `0600`），并在下次启动时删除；如果它也没了，就重置：

```sh
docker exec -e SCRCPYGATE_SHOW_GENERATED_PASSWORD=true scrcpygate python -m app.cli reset-admin
# 或者，在有安装脚本的场景下：
./deploy.sh --reset-admin
```

**容器内生命周期命令**（`app/cli.py`），都可以通过 `docker exec` / `docker compose exec` 执行：

| 命令 | 作用 |
| --- | --- |
| `bootstrap-admin` | 创建管理员账号；需要 `SCRCPYGATE_SHOW_GENERATED_PASSWORD=true` 才会打印生成的密码 |
| `reset-admin` | 生成新的管理员密码（同样需要上面的显示开关） |
| `initial-password` | 若「本次调用刚创建账号」且环境里的密码仍与库中一致，则打印它 |
| `generate-alas-key` | 生成服务端 ALAS 令牌密钥（不会打印密钥值） |
| `alas-token-status` / `migrate-alas-token` | 历史 ALAS 令牌状态（只读）/ 导入并轮换 |

**ALAS 令牌迁移**也可以走安装脚本：

```sh
./deploy.sh --token-status
./deploy.sh --migrate-alas-token
```

**不用 Compose 的 `docker run`** —— bridge 与宿主网络配方、密码处理、USB 直通与手工升级见
[docs/deployment/docker-run.zh.md](docs/deployment/docker-run.zh.md)。它们与两份 compose 文件等价；
一旦走这条路，升级与回滚都由你自己负责。

## 配置

`.env.example` 记录了全部开关（由 `docker compose` 与 `deploy.sh` 读取，应用自身不读）。

| 分组 | 变量 |
| --- | --- |
| 网络 | `WEB_SCRCPY_BIND`、`WEB_SCRCPY_PORT`、`WEB_SCRCPY_DATA_HOST`、`PUBLIC_BASE_URL`、`ALLOWED_HOSTS`、`ALLOWED_ORIGINS`、`TRUST_PROXY`、`TRUSTED_PROXY_IPS` |
| 会话与登录防护 | `SESSION_COOKIE_SECURE`、`MIN_PASSWORD_LENGTH`、`LOGIN_RATE_LIMIT_*`、`LOGIN_CAPTCHA_*` |
| ADB 与设备 | `ADB_AUTOCONNECT`、`ADB_PATH`、`ADB_SERVER_SOCKET`、`ADB_HEARTBEAT_INTERVAL`、`ADB_ROTATION_POLL_INTERVAL`、`ADB_CONNECT_TIMEOUT` |
| 流媒体 | `SCRCPY_STREAM_MODE`、`SCRCPY_SERVER_LOG_LEVEL`、`SCRCPY_I_FRAME_INTERVAL`、`VIDEO_QUEUE_*`、`SCRCPY_RAW_*` |
| 日志与审计 | `LOG_*`、`AUDIT_*`、`VIEWER_WATCH_RETENTION_DAYS` |
| ALAS（可选） | `ALAS_EMBED_ORIGIN`、`ALAS_ALLOWED_HOSTS`、`ALAS_ALLOWED_CIDRS`、`ALAS_POLICY_*`、`ALAS_TOKEN_*` |
| 镜像与构建 | `SCRCPYGATE_IMAGE`、`PYTHON_IMAGE`、`PIP_INDEX_URL` |

- `SCRCPYGATE_IMAGE` 决定跑哪个镜像：留空 = 本地构建的 `scrcpygate:local`；发布时钉不可变引用
  `ghcr.io/<owner>/scrcpygate@sha256:<digest>`。
- 网络慢时构建前设置 `PIP_INDEX_URL` 指向就近的 PyPI 镜像。
- ALAS 令牌加密密钥由你的密钥管理服务注入，或一次性生成到数据目录；**不要**把密钥材料写进 `.env`。

## 安全模型

- Host / Origin 白名单、可选的反向代理信任（显式 CIDR）、写操作 CSRF 令牌、`SameSite`/secure
  会话 Cookie，生产默认关闭 API 文档。
- 登录防护：失败计数、封禁窗口、连续失败后要求内置 PoW 点击验证。
- 设备按用户授权；观看与控制是两种权限；控制是显式租约，可释放也可被接管（两者都进审计）。
- ALAS：出站请求受主机/CIDR 白名单与响应体积上限约束；嵌入式界面执行可见性策略，被拒绝的动作
  记审计。
- 审计事件带哈希链；审计日志有保留与告警上限。
- 账户密码以 PBKDF2-HMAC-SHA256 哈希存储（`pbkdf2_sha256$…`，默认 31 万次迭代、每账户随机盐、
  恒定时间比较），不可逆；**会话令牌以 SHA-256 哈希入库**，因此拿到数据库副本也无法冒充在线会话；
  ALAS 令牌用 AES-GCM 加密，密钥存放在数据库之外。数据库文件本身不加密 —— 那一层交给磁盘/卷加密。
- 「安全机制」页可列出当前登录会话（账户、客户端、来源 IP、最近活动）并**按会话踢出** ——
  踢出会同时关闭该会话正在使用的 WebSocket；`MAX_SESSIONS_PER_USER` 限制单账户并发会话数，
  超出时在登录那一刻淘汰最旧的会话。
- 容器以非 root 运行；compose 样例丢弃全部 capabilities 并启用 `no-new-privileges`。

登录默认使用 ScrcpyGate 自身的 SHA-256 PoW：点击验证按钮后显示加载动画，完成后显示勾选。四道有界小题降低等待波动；签名挑战绑定账号和来源、有有效期且只能成功使用一次。PoW 用来提高自动化成本，不能证明用户是真人，也不能代替限流、TLS 或 WAF。

需要 HTTPS（本机测试可用 localhost）以使用 WebCrypto。默认构建不包含 ALTCHA/Cap 源码或依赖，也不请求外部验证码服务。`LOGIN_POW_PROVIDER=builtin` 选择内置实现；其他提供方需单独安装受信任的适配器包（`scrcpygate.pow` entry point、API v1、专用静态目录及 `client.js`、`issue`/`verify` 方法），仅安装上游库不能直接兼容。Docker 应在派生镜像里安装适配器，再配置其 entry-point 名称并重启。未安装或不兼容的扩展会阻止启动，不会跳过验证。切换后须重新取得挑战；登录限流仍包含进程内状态，不支持多 Worker/多副本部署。

### 后台安全配置

- **访问记录**：点击「明细」原地展开并平滑定位；点击「封禁」弹窗选择时长与原因，改期保留已有原因。封禁会断开现有连接；封禁自己的来源会失去访问权限，可在服务器执行 `./deploy.sh --unban <ip>` 恢复。
- **登录保护**：可调整 PoW 首题难度、失败递增、难度上限、挑战有效期、签发间隔与失败阈值。轻量／均衡／加强预设只修改计算难度，每增加 1 bit 期望计算量约翻倍。先试算并用手机验证；内置计算期限为 30 秒。保存后对新签发的挑战生效。
- **地域限制 → 地区库与自动更新**：填写 MaxMind **Account ID 与 License Key**，无需账户密码。留空保留原值，更换 Account ID 时须同时填写新 Key。保存后点击「立即检查更新」验证下载权限；仅保存不会验证 MaxMind 凭据，也不会开启强制执行。

后台凭据作为私有配置文件保存在 `data/.geo-credentials.json`（或自定义数据目录），**不做文件内容加密**。Linux 权限为 `0600`；Windows 请限制数据目录 ACL。接口不会回显 Key，凭据不进入 Git、Docker 构建上下文、SQLite 或设置导出。`deploy.sh` 会将其纳入私有数据备份，备份须按密钥管理。保留数据重装会保留配置，彻底清理数据会删除配置；仅在后台清除凭据，不会删除地区库或取消地域策略。

环境变量 `GEO_ACCOUNT_ID` 或 `GEO_LICENSE_KEY` 任一非空时，整个环境凭据对优先，因此两项均须配置，不会与后台值混用；后台不能覆盖环境凭据。`GEO_UPDATE_ENABLED=false` 时，即使保存凭据也不会下载，适用于外部管理的只读地区库。

后台显示凭据配置状态、最近检查和最近验证通过的时间。MaxMind 下载认证没有网页登录会话，不提供可查询的账户/Key 到期日；网络故障不会显示为凭据失效，轮换凭据后需重新验证。参见 [MaxMind 更新文档](https://dev.maxmind.com/geoip/updating-databases/) 与 [License Key 文档](https://support.maxmind.com/knowledge-base/articles/using-maxmind-license-keys)。

可在后台保存 1–168 小时的自动检查周期（推荐 12），优先于 `GEO_UPDATE_INTERVAL_HOURS` 默认值，持久化并立即重新调度，无需重启；禁用自动更新的环境开关仍然有效。国家/地区预设仅填入代码，保存地域设置后才生效。访问记录列表按当前地区库查询归属；国家筛选、历史明细和导出仍按记录时的值，内网地址不定位。

默认每 12 小时附加随机延迟检查一次，通过官方 HTTPS 下载并限制重定向目标。检查共用 10 分钟冷却和每日 30 次尝试上限（UTC 日期，失败计入）。远端版本未变时不重复下载，下载或校验失败保留最后可用库。构建超过 30 天的地区库被本项目新鲜度策略视为不可用；强制执行模式下会拒绝访问，启用前请确认更新可用。

建议先「只观察」并预演当前来源。使用 WAF/CDN 时正确配置可信代理网段，不要无条件信任转发头。VPN、代理和移动网络可能影响定位准确性，地域限制不能替代认证与 IP 封禁。误锁恢复：`./deploy.sh --geo-off`，或配置 `GEO_ENFORCE_DISABLED=true` 并重启。

MaxMind 官方文档：[创建 License Key](https://support.maxmind.com/hc/en-us/articles/4407111582235-Generate-a-License-Key) · [下载与更新周期](https://support.maxmind.com/hc/en-us/articles/4408216129947-Download-and-Update-Databases)。GeoLite Country 当前通常每周二、周五更新；定期检查不代表每次都会发布新库。

---

<sub>Apache-2.0，见 [LICENSE](LICENSE) · 随仓库分发的第三方组件见 [THIRD_PARTY.md](THIRD_PARTY.md)</sub>
