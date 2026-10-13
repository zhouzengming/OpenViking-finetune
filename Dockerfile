# syntax=docker/dockerfile:1.9

# 基础镜像参数化，便于把慢/不可达的 registry 换成镜像站。默认值仍是上游引用，
# 裸 `docker build` 行为与上游一致；镜像站默认值放在 docker-compose.build.yml。
# 这些 ARG 声明在任何 FROM 之前，才能被下面每个 FROM 使用。
ARG RUST_IMAGE=rust:1.91.1-trixie
ARG NODE_IMAGE=node:24-trixie-slim
ARG RUNTIME_IMAGE=python:3.13-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:python3.13-trixie-slim
# Debian apt 镜像。留空（默认）保持镜像自带源。用 http:// 而非 https://：运行时
# 阶段的 ca-certificates 正是靠这条 apt 安装的，https 会先有鸡先有蛋。
ARG APT_MIRROR=
# crates.io 源替换。留空（默认）走上游 crates.io。源替换是 checksum 透明的，
# Cargo.lock 照常校验。
ARG CARGO_MIRROR=
# PyPI 镜像，供完全访问不到 pypi.org 的机器构建。两个必须成对设置：PYPI_INDEX
# 既改写 uv.lock 里记录的 index，也决定 uv 解析构建依赖时用的 index；PYPI_FILES
# 改写产物主机。留空（默认）不动 uv.lock。
ARG PYPI_INDEX=
ARG PYPI_FILES=

# Stage 1: provide Rust toolchain (required by setup.py -> build_ov_cli_artifact -> cargo build)
# ragfs-python's default S3-enabled dependency set currently requires rustc >= 1.91.1.
FROM ${RUST_IMAGE} AS rust-toolchain

# Stage 2: build Studio separately so npm failures stop the Docker build.
FROM ${NODE_IMAGE} AS web-studio-builder
ARG TARGETPLATFORM
WORKDIR /app/web-studio

# Keep npm install cached when only Studio sources change.
COPY web-studio/package.json web-studio/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm,id=npm-${TARGETPLATFORM} npm ci
COPY web-studio/ ./
RUN npm run build -- --base=/studio/ \
 && test -f dist/index.html

# Stage 3: build Python environment with uv (builds Rust CLI + C++ extension)
FROM ${UV_IMAGE} AS py-builder

# Reuse Rust toolchain from stage 1 so setup.py can compile ov CLI in-place.
COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo
ENV RUSTUP_HOME=/usr/local/rustup
ENV PATH="/app/.venv/bin:/usr/local/cargo/bin:${PATH}"
ARG OPENVIKING_VERSION=
ARG TARGETPLATFORM
ARG UV_LOCK_STRATEGY=auto

# 把 Debian 源改写成 APT_MIRROR。trixie 用 DEB822 格式
# (/etc/apt/sources.list.d/debian.sources)，且 debian 与 debian-security 都在
# deb.debian.org 下，一次替换覆盖全部 suite。用内层 if 而不是 `[ -f ] && sed`：
# 后者在最后一个 glob 未匹配时会让整个 RUN 返回 1。
ARG APT_MIRROR
RUN if [ -n "${APT_MIRROR}" ]; then \
        for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources \
                 /etc/apt/sources.list.d/*.list; do \
            if [ -f "$f" ]; then \
                sed -i "s|https\?://deb.debian.org|${APT_MIRROR}|g" "$f"; \
            fi; \
        done; \
    fi

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ccache \
    cmake \
    git \
 && rm -rf /var/lib/apt/lists/*

# Route gcc/g++/cc through ccache so cmake (which asks shutil.which("gcc")) picks
# up /usr/lib/ccache/gcc and benefits from the BuildKit cache mount on /root/.ccache.
ENV PATH="/usr/lib/ccache:${PATH}"
ENV CCACHE_DIR=/root/.ccache
# Pin Cargo's target dir to a stable path so a BuildKit cache mount can persist
# build artifacts across layer reruns even when uv builds the wheel in an
# ephemeral isolated tempdir.
ENV CARGO_TARGET_DIR=/cargo-target

# 指向 CARGO_MIRROR。cargo build 的耗时大头是抓几百个 crate 而非编译。
ARG CARGO_MIRROR
RUN if [ -n "${CARGO_MIRROR}" ]; then \
        mkdir -p "${CARGO_HOME}"; \
        printf '[source.crates-io]\nreplace-with = "mirror"\n\n[source.mirror]\nregistry = "%s"\n' \
            "${CARGO_MIRROR}" > "${CARGO_HOME}/config.toml"; \
    fi

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_NO_DEV=1
WORKDIR /app

# Copy source required for setup.py artifact builds and native extension build.
COPY Cargo.toml Cargo.lock ./
COPY pyproject.toml uv.lock setup.py README.md ./

# 把 uv.lock 指向 PYPI_INDEX / PYPI_FILES。在构建期改写而不是改仓库里那份，
# 是为了让 uv.lock 与上游保持逐字节一致；--locked 的严格保证不受影响：包版本与
# 全部 sha256 均不变，镜像站提供的是逐字节相同的产物。
#
# UV_DEFAULT_INDEX 也必须设：sgmllib3k 这类源码包会在构建期经由配置的 index
# 解析自身的构建依赖（setuptools），绕过锁文件。只改 uv.lock 不够。
ARG PYPI_INDEX
ARG PYPI_FILES
ENV UV_DEFAULT_INDEX=${PYPI_INDEX}
RUN if [ -n "${PYPI_INDEX}" ]; then \
        sed -i "s|registry = \"https://pypi.org/simple\"|registry = \"${PYPI_INDEX}\"|g" uv.lock; \
    fi; \
    if [ -n "${PYPI_FILES}" ]; then \
        sed -i "s|https://files.pythonhosted.org|${PYPI_FILES}|g" uv.lock; \
    fi
COPY scripts/build_support/ scripts/build_support/
COPY bot/ bot/
COPY crates/ crates/
COPY openviking/ openviking/
COPY --from=web-studio-builder /app/web-studio/dist/ openviking/web_studio/dist/
COPY openviking_cli/ openviking_cli/
COPY openviking_gateway/ openviking_gateway/
COPY src/ src/
COPY third_party/ third_party/

# Install project and dependencies. setup.py packages the copied Studio bundle
# without running npm again, while build_ext still builds native extensions.
# Default to auto-refreshing uv.lock inside the ephemeral build context when it is
# stale, so Docker builds stay unblocked after dependency changes. Set
# UV_LOCK_STRATEGY=locked to keep fail-fast reproducibility checks.
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-${TARGETPLATFORM} \
    --mount=type=cache,target=/cargo-target,id=cargo-target-${TARGETPLATFORM} \
    --mount=type=cache,target=/usr/local/cargo/registry,id=cargo-registry-${TARGETPLATFORM} \
    --mount=type=cache,target=/usr/local/cargo/git,id=cargo-git-${TARGETPLATFORM} \
    --mount=type=cache,target=/root/.ccache,id=ccache-${TARGETPLATFORM} \
    if [ -n "${OPENVIKING_VERSION:-}" ]; then \
        export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING="${OPENVIKING_VERSION}"; \
    elif [ -f openviking/_version.py ]; then \
        export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING="$(python -c "import runpy; print(runpy.run_path('openviking/_version.py')['version'])")"; \
    else \
        echo "OPENVIKING_VERSION build arg is required when building without openviking/_version.py" >&2; \
        exit 2; \
    fi; \
    case "${UV_LOCK_STRATEGY}" in \
        locked) \
            uv sync --locked --no-editable --reinstall-package openviking --extra bot --extra gemini --extra opengauss --extra gateway \
            ;; \
        auto) \
            if ! uv lock --check; then \
                uv lock; \
            fi; \
            uv sync --locked --no-editable --reinstall-package openviking --extra bot --extra gemini --extra opengauss --extra gateway \
            ;; \
        *) \
            echo "Unsupported UV_LOCK_STRATEGY: ${UV_LOCK_STRATEGY}" >&2; \
            exit 2 \
            ;; \
    esac

# Stage 4: runtime
FROM ${RUNTIME_IMAGE}

# 把 Debian 源改写成 APT_MIRROR。trixie 用 DEB822 格式
# (/etc/apt/sources.list.d/debian.sources)，且 debian 与 debian-security 都在
# deb.debian.org 下，一次替换覆盖全部 suite。用内层 if 而不是 `[ -f ] && sed`：
# 后者在最后一个 glob 未匹配时会让整个 RUN 返回 1。
ARG APT_MIRROR
RUN if [ -n "${APT_MIRROR}" ]; then \
        for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources \
                 /etc/apt/sources.list.d/*.list; do \
            if [ -f "$f" ]; then \
                sed -i "s|https\?://deb.debian.org|${APT_MIRROR}|g" "$f"; \
            fi; \
        done; \
    fi

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

# Resolve relative storage paths inside the persistent mount.
WORKDIR /app/.openviking

COPY --from=py-builder /app/.venv /app/.venv
# Fail the image build if VikingBot and the separately released SDK drift apart.
RUN /app/.venv/bin/python -I -c "import inspect; from importlib.metadata import version; from openviking_sdk.client import AsyncHTTPClient; signature = inspect.signature(AsyncHTTPClient.get_skill); raise SystemExit(0 if 'include_integrity' in signature.parameters else f\"incompatible openviking-sdk {version('openviking-sdk')}: AsyncHTTPClient.get_skill{signature} lacks include_integrity\")"
RUN /app/.venv/bin/python -I -c "from importlib.util import find_spec; from pathlib import Path; spec = find_spec('openviking.web_studio'); locations = list(spec.submodule_search_locations or ()) if spec else []; root = Path('/app/.venv').resolve(); p = (Path(locations[0]).resolve() / 'dist/index.html').resolve() if len(locations) == 1 else None; valid = p is not None and p.is_file() and p.is_relative_to(root); raise SystemExit(0 if valid else f'missing or misplaced Studio bundle: spec_found={spec is not None}, locations={locations!r}, resource={p}')"
COPY deploy/docker/openviking-entrypoint.sh /usr/local/bin/openviking-entrypoint
COPY deploy/docker/pending_health_server.py /usr/local/bin/openviking-pending-health
RUN mkdir -p /app/.openviking \
 && sed -i 's/\r$//' /usr/local/bin/openviking-entrypoint /usr/local/bin/openviking-pending-health \
 && chmod +x /usr/local/bin/openviking-entrypoint /usr/local/bin/openviking-pending-health
ENV HOME="/app" \
    PATH="/app/.venv/bin:$PATH" \
    OPENVIKING_CONFIG_FILE="/app/.openviking/ov.conf" \
    OPENVIKING_CLI_CONFIG_FILE="/app/.openviking/ovcli.conf"

EXPOSE 1933

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["openviking-entrypoint", "--healthcheck"]

# Default persistent state (ov.conf, ovcli.conf, workspace data) lives under
# /app/.openviking, which mirrors the host's ~/.openviking layout. Mount one
# volume there to persist everything across container restarts:
#   docker run -v ~/.openviking:/app/.openviking <image>
# If ov.conf is absent on first start, set OPENVIKING_CONF_CONTENT to the full
# JSON, or `docker exec` in and run `openviking-server init`.
# Override command to run CLI, e.g.:
# docker run --rm -v ~/.openviking:/app/.openviking <image> openviking --help
ENTRYPOINT ["openviking-entrypoint"]
