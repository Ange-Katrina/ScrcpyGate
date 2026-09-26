# CI/CD 与手动部署

`Builds` 工作流负责源码检查、双架构镜像验证和 GHCR 发布，服务器由维护者手动部署。

## 触发规则

| 触发事件 | 检查与镜像测试 | 发布标签 |
| --- | --- | --- |
| 向 `main` / `dev` 提交 PR | amd64、arm64 都验证 | 不发布 |
| 推送 `main` | 全部执行 | `edge`、`sha-<完整提交号>` |
| 推送 `dev` | 全部执行 | `dev`、`sha-<完整提交号>` |
| 推送 `v1.2.3` 标签 | 全部执行 | `v1.2.3`、`latest`、`sha-<完整提交号>` |
| 推送 `v1.2.3-rc.1` 标签 | 全部执行 | 预发布版本、`sha-<完整提交号>` |
| Actions 页面手动运行 | 全部执行 | 按所选分支或版本标签应用上述规则 |

手动运行其他分支时只验证。版本标签采用 `v主版本.次版本.修订号[-预发布标识]`，
预发布版本不会修改 `latest`。服务器建议固定镜像 digest。
不同版本标签可以并行发布：`latest` 跟随最后完成的正式发布，不比较版本号大小。
需要确定先后顺序时，请依次发布正式标签。各分支独立串行，分支构建不会取消
正在排队的版本发布。

## 发布门槛

1. 检查跟踪文件边界、Python/JSON/JavaScript 语法、Ruff、Shell 语法、
   两种 Compose 配置、Python 依赖漏洞、Actions 语法和 Git 历史中的凭据。
2. 使用原生 amd64 与 arm64 runner 构建；各自验证 bridge 与宿主网络启动、
   `/healthz`、`/login`、容器健康状态、非 root 运行和系统 ADB 版本命令。
3. Trivy 阻止包含已有修复版本的 CRITICAL 系统包漏洞的镜像通过。
   Python 依赖漏洞由独立的 `pip-audit` 检查负责。
4. 保存已验证镜像和 SPDX SBOM，通过校验和传递给发布任务，临时构建产物保留一天。
5. 发布任务直接载入这些镜像，不重新构建；生成多架构索引及 provenance/SBOM
   证明后，再更新正式镜像标签。

`build-<运行号>-<重试号>[-架构]` 是中间产物标签。部署时使用成功工作流摘要里的
已验证 digest。正式标签更新前失败时，已有正式标签保持原版本。
真实设备投屏和实际 ALAS 服务联调仍需独立验证。

镜像随附 `/app/LICENSE`、`/app/THIRD_PARTY.md` 和第三方组件声明，两种架构的
启动检查都会验证这些文件。`tools/release_manifest.py` 记录应用与部署输入，
包含两份 Compose 文件和许可证文档；它与 CI 生成的镜像 SPDX SBOM 分工不同。

## 首次配置 GitHub

工作流使用 GitHub 自动提供的 `GITHUB_TOKEN`，不需要在源码中保存 PAT 或服务器凭据。
只有发布任务申请包、证明和 OIDC 写权限，PR 无法进入发布任务。

新镜像包首次发布后，检查包的可见性：**公开仓库不代表 GHCR 包自动公开**。
需要匿名拉取时，在包设置中将可见性改为 **Public**。

如果删除并重建了仓库，但原 GHCR 包还在：

1. 进入账号的 **Packages → scrcpygate → Package settings**。
2. 在 **Manage Actions access** 中添加当前仓库，授予 **Write** 权限；
   同时确认 **Connect repository** 指向当前仓库。
3. 已验证构建产物仍在一天保留期内时，选择 **Re-run failed jobs**；过期后选择
   **Re-run all jobs** 重新构建验证。即使 YAML 已设置 `packages: write`，包授权
   缺失仍会返回 `permission_denied: write_package`。

镜像包含 OCI source 标签用于关联来源，但它不能替代旧包的访问权限设置。
把 `packages: write` 移到工作流顶层，或在 `docker push` 与构建 Action 之间切换，
都不会改变包访问权限。本地 GitHub CLI 登录与工作流自动获得的 `GITHUB_TOKEN`
相互独立；给 CLI 增加 Packages scope 不会为工作流授权。
发布任务成功和失败时都会生成摘要；候选镜像推送失败时，还会显示包授权修复步骤。
请结合失败步骤的日志区分权限、仓库服务或网络故障。

## 发布版本

在发布 PR 中将根目录 `VERSION` 改为目标版本（例如 `1.0.0`）。验证并合入
`main` 后，创建与文件内容匹配的版本标签；两者不一致时 CI 会拒绝发布：

```sh
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

候选版本需要先将 `VERSION` 改为 `1.0.0-rc.1`，再使用标签 `v1.0.0-rc.1`。
递增规则和开发镜像标识见[版本号规范](../contributing/versioning.zh.md)。
在 **Actions → Builds** 中确认三个阶段全部成功，
复制摘要中的 `ghcr.io/<owner>/scrcpygate@sha256:...`。

标签工作流发布的是 Docker 镜像，不会自动创建 GitHub Release。
使用已有标签创建 Release 草稿，按[英文发行模板](../../.github/RELEASE_TEMPLATE.md)
填写说明，并点击 GitHub 的 **Generate release notes** 生成分类后的 PR 列表。
[编写指南](../contributing/pull-requests-and-releases.md#release-notes)说明了标签分类、
更新亮点、升级动作和已验证镜像 digest 的填写方式。完整 PR 列表放在说明末尾，
发布前检查草稿；候选版本同时标记为预发布。

## 更新现有 bridge 部署

更新前在 GHCR 核对目标镜像的已发布标签或 digest。
当前镜像和本地构建读取内置 `VERSION`；旧镜像可能仍只有非版本号标记。
请同时核对产品版本与实际镜像引用。

在当前部署目录执行：

```sh
sudo sh ./deploy.sh --update --image ghcr.io/ange-katrina/scrcpygate:edge
```

稳定部署使用已验证的版本或 digest。不传 `--image` 时先取 `SCRCPYGATE_UPDATE_IMAGE`，
再取 `latest`；只有发布正式版后才存在 `latest`。重复更新浮动标签会先拉取，再比较实际
镜像 ID。此命令要求当前目录拥有正在运行的服务，以及 Docker Compose plugin；host
网络安装请按其独立手动部署步骤更新。

脚本先为原容器的实际镜像创建本地回滚标签，再拉取目标。修改配置前取得 SQLite 在线
快照及配套 ALAS 密钥，并在归档旁保留权限受限的 `.env` 副本。无法取得一致快照就停止
更新。显式使用 `--skip-update-backup` 只跳过数据快照，仍保留配置副本与回滚镜像。

启动仅使用已下载镜像，不重建、不再次拉取。失败时尝试恢复原配置及固定镜像，并检查
镜像身份和健康状态。退出码 2 表示镜像回滚通过，3 表示需要人工恢复。**数据库迁移不会
自动回退**；不兼容迁移需要人工恢复配套数据备份。

日志页面新增默认 30 天的按时间清理。已有行数和文件轮转上限仍生效；“不清理”只关闭
按天删除。升级前请导出仍需保留的审计历史。审计清理保留链锚点，只删除连续过期的前缀。

## 服务器手动部署

已有 bridge 模式部署可使用带健康检查和失败回滚的脚本：

```sh
sh tools/deploy_release.sh \
  --image ghcr.io/<owner>/scrcpygate@sha256:<digest> \
  --compose-dir /path/to/deployment
```

该脚本使用 `compose.yaml`，存在上一版镜像时才能自动回滚。宿主网络模式需要在
服务器 `.env` 中设置 `SCRCPYGATE_IMAGE`，再显式选择配置文件：

```sh
docker compose -f compose.host.yaml pull
docker compose -f compose.host.yaml up -d --no-build
docker compose -f compose.host.yaml ps
```

宿主网络模式应保留上一版 digest，供手动恢复使用。首次安装、数据目录权限、
管理员初始化及 `docker run` 示例见 [README](../../README.zh.md) 和
[Docker 部署文档](docker-run.zh.md)。

Upstream Canary 保持独立实验流程，不会更新 `latest`。
