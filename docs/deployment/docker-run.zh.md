# 高级部署：`docker run`

[English](docker-run.md) · 简体中文 · [返回 README](../../README.zh.md)

受维护的部署路径是 [Docker Compose](../../README.zh.md#docker-compose)。这一页给没有（或不想用）
Compose 的机器：用纯 `docker run` 复现那两份 compose 文件。这里跑的是**同一个镜像**、
**同一个 `app.main:app`**，区别只在网络方式与生命周期工具。

> [!NOTE]
> 走这条路意味着升级、开机自启与回滚都要你自己负责。Compose 只是一份小文件，却免费给你
> `up -d`、`pull`、`logs -f` 和 `restart: unless-stopped` —— 除非有硬性限制，否则建议用 Compose。

## 1. 选择镜像

本项目镜像包已公开，支持 amd64 和 arm64。无需登录即可拉取最新已验证的 `main` 构建：

```sh
IMAGE=ghcr.io/ange-katrina/scrcpygate:edge
docker pull "$IMAGE"
```

需要固定部署版本时，把 `IMAGE` 设置为成功的
[Builds 运行](https://github.com/Ange-Katrina/ScrcpyGate/actions/workflows/Builds.yml)
摘要中的 `ghcr.io/ange-katrina/scrcpygate@sha256:<digest>`，再拉取该引用。
`latest` 只在正式版本发布时创建，目前可能尚不存在。私有 fork 的镜像包则需要
使用有拉取权限的账号登录镜像仓库。

也可以从源码构建：

```sh
git clone https://github.com/Ange-Katrina/ScrcpyGate.git
cd ScrcpyGate
IMAGE=scrcpygate:local
docker build -t "$IMAGE" .                       # 网络慢时加 --build-arg PIP_INDEX_URL=<镜像源>
```

请在同一个 Shell 中执行后续命令，以保留 `IMAGE` 的选择。

## 2. 准备数据目录

容器以 `uid 100(app):gid 101(app)` 运行。你挂载的宿主目录必须可被这个用户写入，否则应用启动即
`PermissionError: /app/data/.init-db.lock`，容器会反复重启：

```sh
mkdir -p ./data && sudo chown -R 100:101 ./data
```

## 3. bridge 网络 + 端口映射

这就是 [`compose.yaml`](../../compose.yaml) 的做法。首启管理员密码用不回显的方式读入（长度至少
`MIN_PASSWORD_LENGTH`＝12），并**按变量名**传给容器，这样密码不会出现在命令行或 shell 历史里：

```sh
read -rs INITIAL_ADMIN_PASSWORD && export INITIAL_ADMIN_PASSWORD

docker run -d --name scrcpygate --restart unless-stopped \
  -p 127.0.0.1:5000:5000 \
  -e INITIAL_ADMIN_PASSWORD \
  -e PUBLIC_BASE_URL=http://127.0.0.1:5000 \
  -e ALLOWED_HOSTS=127.0.0.1,localhost \
  --cap-drop ALL --security-opt no-new-privileges \
  --log-driver local --log-opt max-size=20m --log-opt max-file=5 \
  -v "$PWD/data:/app/data" \
  "$IMAGE"
```

然后打开 `http://127.0.0.1:5000`。对外提供服务前，`-p` 与 `PUBLIC_BASE_URL` 要一起改；在反向代理 /
WAF 终止 TLS 之前，绑定地址请保持在 `127.0.0.1`。

## 4. 宿主网络

这就是 [`compose.host.yaml`](../../compose.host.yaml) 的做法。没有端口映射 —— 容器通过
`WEB_SCRCPY_BIND` / `WEB_SCRCPY_PORT` 自己绑宿主端口，此时容器里的 `127.0.0.1` **就是宿主**：

```sh
read -rs INITIAL_ADMIN_PASSWORD && export INITIAL_ADMIN_PASSWORD

docker run -d --name scrcpygate --restart unless-stopped --net=host \
  -e WEB_SCRCPY_BIND=0.0.0.0 -e WEB_SCRCPY_PORT=5000 \
  -e INITIAL_ADMIN_PASSWORD \
  -e PUBLIC_BASE_URL=http://127.0.0.1:5000 \
  -e ALLOWED_HOSTS=127.0.0.1,localhost \
  -e ADB_SERVER_SOCKET=tcp:127.0.0.1:5037 \
  --cap-drop ALL --security-opt no-new-privileges \
  --log-driver local --log-opt max-size=20m --log-opt max-file=5 \
  -v "$PWD/data:/app/data" \
  "$IMAGE"
```

`WEB_SCRCPY_BIND` / `WEB_SCRCPY_PORT` 必须与 `PUBLIC_BASE_URL` 保持一致：这个模式下应用是真的绑在那里。

**复用宿主的 adb server。** 配上 `--net=host` 后，`ADB_SERVER_SOCKET=tcp:127.0.0.1:5037` 会把容器的
adb 客户端指到你已经在宿主上跑着的那个 server —— 也就是 `adb devices` 里能看到 USB 设备的那个。
不设这个变量时，容器会自己起一个 adb server，这正是纯网络 ADB（`adb connect`）需要的。

**把 USB 直接给容器。** 想去掉 `ADB_SERVER_SOCKET`、直接把 USB 设备交给容器，就加上
`--device /dev/bus/usb --privileged`（或等价的 udev/cgroup 规则）。复用宿主的 adb server 通常更省事，
也省掉这些额外权限。

## 5. 生命周期与升级

容器内 ADB 的授权密钥保存在 `/app/data/.android`，复用同一数据卷重建后仍保留。
旧镜像使用 `/tmp/.android`。删除旧容器前，先停止投屏并执行一次迁移（不输出密钥内容）：

```sh
docker exec scrcpygate sh -c 'if [ -d /tmp/.android ]; then test ! -e /app/data/.android || exit 1; cp -a /tmp/.android /app/data/.android; fi'
```

目标目录已存在时命令会拒绝覆盖，请先确认它是否已有所需身份。旧容器已删除时，需要从备份恢复密钥，
或在设备上重新授权。复用宿主 ADB server 时不需要此迁移。
新容器入口会配置 ALAS 加密密钥；已有加密数据应恢复原密钥，不能靠生成替代密钥解密。
将公开 URL 改为 HTTPS 后，会自动启用 Secure Cookie；不要继续显式设置 `SESSION_COOKIE_SECURE=false`。

```sh
docker logs -f scrcpygate            # 跟踪日志（stdout 为 JSON）
docker restart scrcpygate            # 重启
docker stop scrcpygate && docker rm scrcpygate   # 移除容器（数据仍在 ./data）
```

升级的方式是「删旧容器、用新的 checkout 构建或按 digest 拉取后重建」—— `docker run` 没有
`up -d --build` 的等价物，所以移除旧容器后重复上面那条 `docker run` 即可。状态都在 `./data` 里，
只要挂载点不变就是安全的。

## 6. 不用 Compose 的管理

容器内的 CLI 与 Compose 说明里的是同一套，只是命令前缀不同：

```sh
docker exec scrcpygate python -m app.cli reset-admin
docker exec scrcpygate python -m app.cli generate-alas-key
docker exec scrcpygate python -m app.cli alas-token-status
```

需要打印密码时必须逐次显式授权，例如：

```sh
docker exec -e SCRCPYGATE_SHOW_GENERATED_PASSWORD=true scrcpygate python -m app.cli reset-admin
```

健康检查：`GET /healthz`。如果创建管理员账号时没有传 `INITIAL_ADMIN_PASSWORD`，生成的密码会被写进
`data/initial_admin_password.txt`（权限 `0600`），并在下次启动时删除；如果它也没了，就用上面这条命令找回。

## 与 Compose 的对应关系

| 本页 | Compose 等价物 |
| --- | --- |
| `docker run -p …`（第 3 节） | 用 `compose.yaml` 的 `docker compose up -d` |
| `docker run --net=host …`（第 4 节） | `docker compose -f compose.host.yaml up -d` |
| 手工重建重跑（第 5 节） | `docker compose up -d --build`、`docker compose pull` |
| `docker exec …`（第 6 节） | `docker compose exec scrcpygate …` |

环境变量、卷与安全姿态（`cap_drop`、`no-new-privileges`、日志上限）都定义在 compose 文件里 ——
如果上面这些 flag 不够用，直接读那两份文件并翻译对应条目。
