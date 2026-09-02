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

### 基础镜像源

Docker Hub 的 rust / node / python 三个基础镜像**默认走 `docker.m.daocloud.io`**。
本机 `/etc/docker/daemon.json` 里 daemon 拉镜像走 socks5 代理，而 `no-proxy` 放行了
`*.daocloud.io`，所以走镜像站既换了源也绕开代理瓶颈。回官方源：`OV_DOCKERHUB=docker.io`。

注意这只是 compose 这层的默认值；`Dockerfile` 里 `RUST_IMAGE` / `NODE_IMAGE` /
`RUNTIME_IMAGE` 三个 ARG 的默认值仍是上游引用，裸 `docker build` 行为不变。

uv 构建镜像（ghcr.io）同样参数化，默认走 `ghcr.m.daocloud.io`，
回官方源用 `OV_GHCR=ghcr.io`。四个基础镜像的元数据均已验证可从镜像站解析。

⚠️ **仍有一处硬编码的 docker.io 依赖**：Dockerfile 第一行的
`# syntax=docker/dockerfile:1.9`。syntax 指令在 ARG 解析之前生效，无法参数化。
完全访问不到 docker.io 的机器需要先手工把该前端镜像准备到本地：

    docker pull docker.m.daocloud.io/docker/dockerfile:1.9
    docker tag  docker.m.daocloud.io/docker/dockerfile:1.9 docker/dockerfile:1.9



### cargo 源

crates.io 默认走 USTC：`sparse+https://mirrors.ustc.edu.cn/crates.io-index/`。
用官方源：`OV_CARGO_MIRROR=`（置空）。实测 index 拉取 0.156s vs 直连 3.48s。

`cargo build --release` 的耗时大头是抓几百个 crate 而非编译——诊断依据：cargo
进程 `State: S`、单线程、零子进程、无 rustc 进程、整机 CPU 97% 空闲。真正编译时
会有十几个 rustc 并行、CPU 打满。源替换是 checksum 透明的，`Cargo.lock` 照常校验。

### PyPI 源

默认走**阿里云**（`PYPI_INDEX` / `PYPI_FILES`），供**完全访问不到 pypi.org 的机器**构建。

选型是实测的（本机、全程 `--noproxy '*'`、3 个不同大小的 wheel 取平均下载速度）：

| 镜像 | 平均 MB/s |
|---|---|
| **阿里云** | **1.83** |
| 华为云 | 1.73 |
| NJU 南大 | 1.10 |
| BFSU 北外 | 1.08 |
| USTC | 0.36 |

USTC 慢约 5 倍。它另有一处小损耗：`/pypi/web/packages/...` 会 301 跳到
`/pypi/packages/...`（仍在站内），每个产物多一次往返 ×581 个包——但实测把路径改成
直连形式也只有 0.38 MB/s，说明主因是带宽而非重定向。阿里云直接 200 无跳转。
回官方源：`OV_PYPI_INDEX= OV_PYPI_FILES=`（两者一起置空）。

做法是**在构建期改写 `uv.lock`**，仓库里那份保持与上游逐字节一致——避免 4217 处
改动带来的永久合并冲突。`--locked` 的严格保证不受影响：包版本与全部 3857 个
sha256 均不变，因为镜像站提供的是逐字节相同的产物（已实测同一 wheel 两处 sha256
完全一致）。

⚠️ **两个变量必须成对设置，只改 uv.lock 不够。** `uv.lock` 钉的是两层：
`registry`（360 处，决定一致性校验）和 `url`（3857 处，决定实际下载地址）。但还有
第三条隐藏路径——`sgmllib3k`、`fusepy` 这类**源码包**在构建期解析自身的构建依赖
（setuptools）时走的是**配置的 index**，绕过锁文件。所以 Dockerfile 里同时设了
`ENV UV_DEFAULT_INDEX=${PYPI_INDEX}`。

该结论由一次**把 pypi.org 与 files.pythonhosted.org 解析到 127.0.0.1 的黑洞构建**
得出：只改锁文件时 `sgmllib3k` 因拉不到 setuptools 而 `Connection refused` 失败；
补上 `UV_DEFAULT_INDEX` 后 581 个包全部装成。注意 `uv lock --check` **发现不了**
这个缺口——它只验一致性，不验可达性。

`PYPI_INDEX` 为空时 `UV_DEFAULT_INDEX` 会是空字符串，实测被 uv 正确忽略，
与完全不设该变量行为一致，故默认路径不受影响。

### apt 源

Debian apt 源默认走 `http://mirrors.ustc.edu.cn`（构建容器直连 `deb.debian.org`
在国内很慢，而构建期代理默认为空）。用镜像自带源：`OV_APT_MIRROR=`（置空）。

几个实现要点：

- trixie 用 DEB822 格式 `/etc/apt/sources.list.d/debian.sources`，不是老的
  `sources.list`；且 debian 与 debian-security **都在 `deb.debian.org`**，
  一次替换即可覆盖三个 suite。
- 用 `http://` 而非 `https://`：运行时阶段的 `ca-certificates` 正是在这条 apt 里
  才安装的，https 会先有鸡先有蛋。apt 包本身有 GPG 签名，不依赖 TLS。
- compose 里用 `${OV_APT_MIRROR-...}`（单减号）而非 `:-`，这样显式置空能被尊重，
  不会因为值为空又回落到默认。
- 改写循环用内层 `if [ -f ]` 而不是 `[ -f ] && sed`：后者在最后一个 glob 未匹配时
  会让整个 `RUN` 返回 1，构建直接失败。

⚠️ `COPY --from` 不做 ARG 插值，只接受字面镜像引用或 stage 名。所以 node 用的是命名
stage `node-toolchain`，不能写成 `COPY --from=${NODE_IMAGE}`（会报 invalid reference format）。

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
