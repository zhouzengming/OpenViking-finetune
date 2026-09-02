# BIE 自用分支：从源码构建并启动容器

本仓库是 [OpenViking](https://github.com/volcengine/OpenViking) 的 fork，`bie-main-v0.4.16`
分支在上游 `v0.4.16`（`499995f3`）基础上带了一批 BIE 自用修复。本文只讲一件事：
**怎么把当前源码构建成镜像并跑起来**。

镜像源、锁文件改写等设计取舍的完整推导在 [`docker/bie-overlay/README.md`](docker/bie-overlay/README.md)，
这里只给操作路径。

## 先选路线

| 你改了什么 | 走哪条 | 耗时 |
|---|---|---|
| 只改 `.py`，没动依赖 | 叠加构建 `docker/bie-overlay/build.sh` | 秒级 |
| 动了依赖 / Rust CLI / C++ 扩展 / web-studio | **完整构建**（本文主线） | 冷构建约 10 分钟 |

拿不准就走完整构建。叠加脚本自带守卫：`pyproject.toml` / `uv.lock` / `Cargo.lock`
相对基线 tag 一变就拒绝构建，不会让你在错误的前提上继续。

## 前置条件

- Docker + **BuildKit**（Dockerfile 用了 `--mount=type=cache`，非 BuildKit 起不来）
- 约 15 GB 空闲磁盘
- crates.io / npm / PyPI 三个源可达（默认已换成国内镜像站，见下）
- **不需要** git submodule，依赖全部 vendored

## 完整构建 + 启动

```bash
OPENVIKING_VERSION=$(git describe --tags --match 'v[0-9]*' | sed 's/^v//; s/-\([0-9]*\)-g/+\1.g/') \
  docker compose -f docker-compose.build.yml up -d --build
```

`OPENVIKING_VERSION` 是**必填**的，compose 用 `:?` 强制。原因见文末「版本号」。

### 受限网络：先把 syntax 前端镜像备到本地

四个基础镜像、apt、cargo、PyPI 源在 `docker-compose.build.yml` 里**已经默认走镜像站**，
无需额外设置。唯一参数化不掉的是 `Dockerfile` 第一行：

```
# syntax=docker/dockerfile:1.9
```

`syntax` 指令在 ARG 解析之前生效，BuildKit 必须先解析这个前端镜像引用。
**关键点：BuildKit 解析 frontend 时不走 `/etc/docker/daemon.json` 里的 `proxies`**——
实测报错是 `dial tcp 199.59.148.8:443: i/o timeout`，直连 IP，代理没参与。
而 `docker pull` 是 daemon 自己拉，**会**用 daemon.json 的代理。

所以在这类机器上，只要把前端镜像**预先放进本地镜像库**，BuildKit 就不再远程解析，
上面那条一条命令即可正常跑：

```bash
docker pull docker/dockerfile:1.9        # 一次即可，之后一直有效
```

daemon 也连不上 docker.io 时，改用镜像站再打上原名：

```bash
docker pull docker.m.daocloud.io/docker/dockerfile:1.9
docker tag  docker.m.daocloud.io/docker/dockerfile:1.9 docker/dockerfile:1.9
```

实测对照（最小 Dockerfile，同样带 `# syntax=`）：

| 本地镜像库有 `docker/dockerfile:1.9` | 结果 |
|---|---|
| 无 | ❌ 去 `auth.docker.io`，超时失败在第 1 行 |
| 有 | ✅ 直接 DONE，不再有网络请求 |

#### 备选：`BUILDKIT_SYNTAX`

不想动本地镜像库的话，也可以用 build-arg 覆盖前端引用。代价是**必须拆成两步**，
因为 `up -d --build` 不接受 `--build-arg`：

```bash
V=$(git describe --tags --match 'v[0-9]*' | sed 's/^v//; s/-\([0-9]*\)-g/+\1.g/')

OPENVIKING_VERSION=$V docker compose -f docker-compose.build.yml build \
  --build-arg BUILDKIT_SYNTAX=docker.m.daocloud.io/docker/dockerfile:1.9

OPENVIKING_VERSION=$V docker compose -f docker-compose.build.yml up -d
```

⚠️ **必须是 `--build-arg`，环境变量无效**：

| 形式 | 结果 |
|---|---|
| `docker build --build-arg BUILDKIT_SYNTAX=...` | ✅ 成功 |
| `BUILDKIT_SYNTAX=... docker build` | ❌ 仍去 `auth.docker.io`，失败 |
| 都不给（且本地无该镜像） | ❌ 失败 |

推荐前者：预拉一次前端镜像之后，日常就只剩最上面那一条命令。

### 构建耗时与「看起来卡住了」

冷构建约 10 分钟，其中 apt 与 cargo 抓包占大头。**不要用 `| tail -N` 看进度**——
会缓冲住输出，让人误以为卡死（这个坑我自己踩过两次）。要么直接看，要么重定向到
文件再 `tail -f`：

```bash
OPENVIKING_VERSION=$V docker compose -f docker-compose.build.yml build > /tmp/ov-build.log 2>&1 &
tail -f /tmp/ov-build.log
```

## 首次启动：配置文件

运行时状态放在**仓库外**，默认 `~/.openviking-container/`：

```
~/.openviking-container/
├── .openviking/          → 挂到 /app/.openviking
│   ├── ov.conf           ← 服务配置，必须有
│   └── ovcli.conf
└── data/                 → 挂到 /app/data
```

`ov.conf` 不存在时容器**不会崩**：entrypoint 起一个占位健康服务，所有 HTTP 请求返回
503 并说明缺什么。补齐配置后它会自动继续启动。三种补法：

```bash
# 1) 从宿主机已有配置复制（最常用）
mkdir -p ~/.openviking-container/.openviking
cp ~/.openviking/ov.conf ~/.openviking-container/.openviking/

# 2) 用环境变量传完整 JSON
OPENVIKING_CONF_CONTENT="$(cat ov.conf)" docker compose -f docker-compose.build.yml up -d

# 3) 进容器初始化
docker exec -it openviking openviking-server init
```

## 验证跑起来的是刚构建的东西

```bash
# 服务健康 + 版本号应等于你传进去的 OPENVIKING_VERSION
curl -s http://127.0.0.1:1933/health
# {"status":"ok","healthy":true,"version":"0.4.16+29.g459c7e01","auth_mode":"api_key"}

# 容器用的镜像 ID 与刚构建的镜像是否一致
[ "$(docker inspect openviking --format '{{.Image}}')" \
  = "$(docker image inspect openviking:source --format '{{.Id}}')" ] \
  && echo "镜像一致" || echo "容器是旧的"
```

版本号带 `+<距离>.g<短 sha>` 后缀时，`git describe` 的口径与你的 HEAD 一致即为对上。

## 可调变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `OPENVIKING_VERSION` | **必填** | 版本号，写进 `_version.py` |
| `OPENVIKING_IMAGE_TAG` | `source` | 镜像 tag（`openviking:<tag>`） |
| `OPENVIKING_CONTAINER_NAME` | `openviking` | 容器名 |
| `OPENVIKING_SERVER_PORT` | `1933` | 宿主机端口 |
| `OPENVIKING_STATE_DIR` | `~/.openviking-container` | 状态目录 |
| `UV_LOCK_STRATEGY` | `locked` | `locked` 锁文件对不上即失败；`auto` 自动重锁 |
| `OV_DOCKERHUB` | `docker.m.daocloud.io` | 置 `docker.io` 回官方源 |
| `OV_GHCR` | `ghcr.m.daocloud.io` | uv 构建镜像源 |
| `OV_APT_MIRROR` | USTC | 置空用镜像自带源 |
| `OV_CARGO_MIRROR` | USTC | 置空回 crates.io |
| `OV_PYPI_INDEX` / `OV_PYPI_FILES` | USTC | **必须成对**置空才回 pypi.org |
| `OV_BUILD_HTTP_PROXY` | 空 | 构建期代理，刻意不读 `HTTP_PROXY` |

代理用独立变量名是有意的：shell 里的 `socks5://` 被静默继承进构建容器会出问题
（多数构建工具只认 `http://`，且 uv 经本地代理有僵死前科）。

## 构建到底编译出了什么

完整构建跑三条编译链，产物全部落进 `site-packages/openviking/`，运行阶段只
`COPY --from=py-builder /app/.venv`——**Rust / Node / cmake 工具链不会进最终镜像**。

| 产物 | 来源 | 体积 | 服务是否用 |
|---|---|---|---|
| `lib/ragfs_python.abi3.so` | Rust | 23 MB | ✅ 服务进程已映射 |
| `storage/vectordb/engine/_x86_{sse3,avx2,avx512}.abi3.so` | C++/CMake | 7.8 MB | ✅ 按 CPU 运行时分发，选中其一 |
| `web_studio/dist/**` | Node/Vite | 15 MB | ✅ 挂在 `/studio/` |
| `bin/ov` | Rust（`crates/ov_cli`） | 13 MB | ❌ **服务从不调用**，只有人在容器里敲 `ov` 才用 |

核验方式是读服务进程的 `/proc/<pid>/maps`，不是看文件在不在：

```bash
docker exec openviking python -c "
import os
pid = max((p for p in os.listdir('/proc') if p.isdigit()
           and 'openviking-server' in open(f'/proc/{p}/cmdline','rb').read().decode('utf8','replace')),
          key=lambda p: len(os.listdir(f'/proc/{p}/task')))
print(*sorted({l.split()[-1] for l in open(f'/proc/{pid}/maps')
               if l.rstrip().endswith('.so') and 'site-packages/openviking' in l}), sep='\n')"
```

（别写死 pid：服务 pid 取决于 entrypoint 起进程的顺序，不同镜像版本会变。）

三个 SIMD 变量不是冗余：`_x86_caps` 探 CPU，按 `avx512 → avx2 → sse3` 挑一个，
同一镜像换机器会选不同的那个。

## 日常更新（纯 Python 改动）

```bash
docker/bie-overlay/build.sh          # 在官方 v0.4.16 镜像上只覆盖改动的 .py
cd ~/.containers/openviking && docker compose up -d
```

细节与前提见 [`docker/bie-overlay/README.md`](docker/bie-overlay/README.md)。

## 排障

**构建第 1 行就失败，报 `auth.docker.io` 超时**
→ `# syntax=` 前端拉不到。`docker pull docker/dockerfile:1.9` 一次即可，见上面「受限网络」。
注意 BuildKit 解析 frontend **不走** daemon.json 的代理，所以「`docker pull` 能成功但
构建失败」是正常现象，不矛盾。

**构建长时间没输出，像是卡死了**
→ 先确认不是你自己用 `| tail -N` 把输出缓冲住了。冷构建约 10 分钟，apt 与 cargo
抓包占大头。见上面「构建耗时」。

**构建 `exit 2`，说要 `OPENVIKING_VERSION`**
→ `openviking/_version.py` 受 `.gitignore` 管理、不入版本库。全新 clone 上它不存在
所以直接失败；开发机上有残留又会读到**过期版本号且不报错**（实测 `0.1.dev873`）。
两种机器行为不一致，所以必须显式传，别依赖回退。

**cargo 阶段卡很久**
→ 大头是抓几百个 crate 而非编译。判据：`cargo` 进程 `State: S`、单线程、零 rustc
子进程、整机 CPU 空闲。真正编译时会有十几个 rustc 并行、CPU 打满。默认已走 USTC。

**容器起来了但所有请求 503**
→ `ov.conf` 缺失，占位健康服务在响应。见「首次启动」。

**与既有实例共用了 workspace**
→ 不要这么做。`service/core.py` 的 pid 锁使两个进程无法共享同一数据目录，
并发访问还有数据损坏风险。验证新镜像请用独立端口 + 独立 `OPENVIKING_STATE_DIR`。

**从旧的「官方镜像 + entrypoint 补丁」方案切过来**
→ 必须移除 compose 里的补丁包装。新镜像已内含那些修复，`apply_*_patch.py`
会因锚点消失而 `exit 1` 拒绝启动（刻意的 fail-stop）。
