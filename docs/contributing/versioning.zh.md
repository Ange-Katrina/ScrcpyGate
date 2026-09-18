# 版本号规范

项目根目录的 [`VERSION`](../../VERSION) 是产品版本号的统一来源，初始值为
`1.0.0`。文件内不带 `v` 前缀，Git 标签带前缀，例如 `v1.0.0`。

| 变化 | 示例 |
| --- | --- |
| 兼容性修复、安全修复 | `1.0.0` → `1.0.1` |
| 向后兼容的新功能 | `1.0.0` → `1.1.0` |
| 不兼容的接口、配置或部署变化 | `1.0.0` → `2.0.0` |
| 候选版本 | `1.1.0-rc.1` → `1.1.0` |

不要每次提交都增加正式版本号。在发布 PR 中修改 `VERSION`，使用英文标题和
更新说明，说明必要的升级操作。源码中的版本号不代表该版本已经发布到 GitHub
或 GHCR。

## 显示与构建

- 源码运行、本地 Docker 和 Compose 构建读取 `VERSION`。
- 管理后台侧栏、系统更新面板、登录页底部和 OpenAPI 版本保持一致。
- CI 分支构建附带开发标识，例如 `1.0.0-dev.main.g0123456789ab`；PR 使用
  `dev.pr`，开发分支使用 `dev.dev`。这些标识包含提交缩写，方便追踪具体镜像。
- 标签构建必须与 `VERSION` 完全匹配；`VERSION=1.0.0` 时仅接受 `v1.0.0`。
  CI 同时写入 OCI 镜像版本标签。
- `main` 镜像别名仍为 `edge`，`dev` 分支仍为 `dev`。正式版本发布 `latest`，
  候选版本不会覆盖 `latest`。
- 旧 `.env` 中的 `SCRCPYGATE_VERSION` 仅作为旧部署兼容回退，不能覆盖当前
  镜像内的版本，也不会覆盖新的 Compose 源码构建。
- 直接使用 `docker build --build-arg SCRCPYGATE_VERSION=...` 时可以指定构建
  版本，必须使用不带 `v` 的有效版本号。分支名、`latest`、digest 和带 `+`
  的构建元数据不能作为该构建参数。

本地查看：`python -m app.version`。
容器查看：`docker exec scrcpygate python -m app.version`。

## 发布顺序

1. 在发布 PR 中修改 `VERSION`，完成验证和英文更新说明。
2. 合并后，为已验证提交创建并推送匹配的带注释 Git 标签。
3. 等待 **Builds** 的双架构验证与 GHCR 发布成功。
4. 使用已有标签创建 GitHub Release，记录验证过的镜像 digest；候选版本标为预发布。

具体操作见 [CI/CD](../deployment/ci-cd.zh.md) 和
[PR 与更新说明规范](pull-requests-and-releases.md)。修改 `VERSION` 本身不会创建
标签或 GitHub Release，也不会自动发布正式版镜像。
