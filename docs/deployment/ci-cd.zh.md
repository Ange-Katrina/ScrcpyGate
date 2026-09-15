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

代码合入 `main` 后创建版本标签，例如：

```sh
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

候选版本使用 `v1.0.0-rc.1`。在 **Actions → Builds** 中确认三个阶段全部成功，
复制摘要中的 `ghcr.io/<owner>/scrcpygate@sha256:...`。

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
