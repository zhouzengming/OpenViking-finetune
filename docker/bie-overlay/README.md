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

    docker build -t openviking:dev .

改动涉及依赖、Rust CLI、原生扩展或 web-studio 时必须走这条。需要 Rust 工具链、
Node、以及可用的 PyPI/crates/npm 网络。注意本机 uv 走本地代理曾出现僵死，
必要时用 `env -u *_proxy UV_DEFAULT_INDEX=<国内镜像>` 绕行。

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
