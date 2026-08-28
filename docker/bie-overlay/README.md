# 把当前源码打包成容器

两条路线，按需求选。

## 路线 A：叠加构建（推荐，秒级）

    docker/bie-overlay/build.sh [镜像标签]     # 默认 openviking:v0.4.16-bie.1

在官方 `v0.4.16` 镜像上叠加本仓库相对基线 tag 的源码改动。

适用前提：改动是**纯 Python 且未新增依赖**。脚本会校验 `pyproject.toml` /
`uv.lock` / `Cargo.lock` 相对基线未变，不满足即拒绝构建。

为什么可行：上游 Dockerfile 是 Rust + Node + C++ 扩展 + web-studio SPA + uv sync
的多阶段构建，耗时长且需要大量网络。而叠加只替换改动的 `.py`，依赖与已测环境
逐字节一致，构建产物（原生扩展、`web_studio/`、`_version.py`）原样保留——
它们不受 git 跟踪，因此不会被 overlay 覆盖。

## 路线 B：完整构建（仓库根 Dockerfile）

改动涉及依赖、Rust CLI、原生扩展或 web-studio 时必须走这条。用仓库根的
`docker-compose.build.yml` 一条命令完成构建 + 启动：

    OPENVIKING_VERSION=$(git describe --tags --match 'v[0-9]*' | sed 's/^v//; s/-\([0-9]*\)-g/+\1.g/') \
      docker compose -f docker-compose.build.yml up -d --build

`OPENVIKING_VERSION` 是**必填**的（compose 用 `:?` 强制）。原因见下节。
其余可调变量：`OPENVIKING_IMAGE_TAG`、`OPENVIKING_SERVER_PORT`、
`OPENVIKING_STATE_DIR`、`OPENVIKING_CONTAINER_NAME`、`UV_LOCK_STRATEGY`
（默认 `locked`，锁文件对不上即失败）。

代理刻意用 `OV_BUILD_HTTP_PROXY` 等独立变量名，不直接读 `HTTP_PROXY`——
避免 shell 里的 `socks5://` 被静默继承进构建容器（多数构建工具只认 `http://`，
且 uv 经本地代理有僵死前科）。

新机器需要：BuildKit（Dockerfile 用了 `--mount=type=cache`）、约 15GB 磁盘、
crates.io + npm + PyPI 三个源可达。**不需要** submodule，依赖全部 vendored。

### 版本号：换机器必踩的坑

`openviking/_version.py` 受 `.gitignore` 管理、不入版本库，而 Dockerfile 的版本
解析是「build arg → `_version.py` → exit 2」：

- **全新 clone**：文件不存在 → 构建 `exit 2` 失败
- **开发机**：文件残留 → 读到过期版本号（实测 `0.1.dev873`）且**不报错**

两种机器行为不一致，所以务必显式传 build arg，不要依赖回退。

## 日常更新流程

改完源码后两条命令：

    docker/bie-overlay/build.sh                                   # 可从任意目录用绝对路径调用
    cd /home/zzm/.containers/openviking && docker compose up -d

同名 tag 重建会产生新的镜像 ID，`compose up -d` 据此判定并重建容器（已实测）。

自检（确认跑的就是刚构建的）：

    [ "$(docker inspect openviking --format '{{.Image}}')" \
      = "$(docker image inspect openviking:v0.4.16-bie.1 --format '{{.Id}}')" ] \
      && echo "镜像一致" || echo "容器是旧的"
    docker exec openviking sh -c 'echo $OPENVIKING_SOURCE_COMMIT'   # 应等于源码 HEAD

## 切换运行时的两个必做检查

1. **移除 entrypoint 补丁包装**。旧 compose 用 `apply_rerank_patch.py` /
   `apply_semantic_queue_patch.py` 在启动时打补丁。新镜像已内含这些修复，
   补丁脚本会因锚点消失而 `exit 1` **拒绝启动**（刻意设计的 fail-stop）。
   entrypoint 应还原为镜像默认。

2. **不要与既有实例共用 workspace**。`service/core.py` 的 pid 锁使两个进程
   无法共享同一数据目录（见缺陷台账 B8），并发访问还有数据损坏风险。
   验证新镜像请用独立端口 + 独立数据目录。

## 可选：开启请求 deadline

新镜像支持 `server.request_timeout_seconds`（ov.conf），**默认 0 即关闭**。
开启后作用于全部 `run_operation` 调用点，取值需高于任何合法长操作——
`resources.add_resource` 在 `wait=true` 时会阻塞整个摄入过程。
