#!/usr/bin/env bash
# 把当前源码状态打包成可运行镜像。
#
# 做法：在官方 v0.4.16 镜像上叠加本仓库相对基线 tag 的改动，而不是重跑上游那套
# Rust + Node + C++ 扩展 + web-studio + uv sync 的多阶段构建。前提是改动为纯
# Python 且未新增依赖——脚本会强制校验这一点，不满足就拒绝构建并提示改走
# 仓库根 Dockerfile 的完整构建。
#
# 用法: docker/bie-overlay/build.sh [镜像标签]
set -euo pipefail

BASE_TAG="${BASE_TAG:-499995f3}"          # 官方 v0.4.16
BASE_IMAGE="${BASE_IMAGE:-ghcr.io/volcengine/openviking:v0.4.16@sha256:46f9e34cd37238c28cbd9535033773d179006bdf7f3e528dd1c46567abce7701}"
SITE_PACKAGES="${SITE_PACKAGES:-/app/.venv/lib/python3.13/site-packages}"
IMAGE="${1:-openviking:v0.4.16-bie.1}"

REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$REPO"
COMMIT="$(git rev-parse --short HEAD)"

# 守卫：依赖一旦变化，叠加就不再等价于真实构建。
if ! git diff --quiet "$BASE_TAG" HEAD -- pyproject.toml uv.lock Cargo.lock; then
  echo "ERROR: 依赖清单相对 $BASE_TAG 已变化，叠加方式不再有效。" >&2
  echo "       请改用仓库根 Dockerfile 做完整构建。" >&2
  exit 1
fi

CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT
mkdir -p "$CTX/overlay"

# 只取两个 Python 包内改动的文件：测试与构建工具（docker/ 等）不入镜像。
# 构建产物如 _version.py 未被 git 跟踪，因此不会卷入，镜像内原有产物得以保留。
git diff --name-only "$BASE_TAG" HEAD \
  | grep -E '^(openviking|openviking_cli)/' \
  | grep -v '^tests/' > "$CTX/changed.txt" || true

HAS_STUDIO_CHANGES=0
if ! git diff --quiet "$BASE_TAG" HEAD -- web-studio/; then
  HAS_STUDIO_CHANGES=1
fi

if [ ! -s "$CTX/changed.txt" ] && [ "$HAS_STUDIO_CHANGES" -eq 0 ]; then
  echo "ERROR: 相对 $BASE_TAG 没有需要叠加的包内或 web-studio 源码改动。" >&2
  exit 1
fi
while read -r f; do
  [ -z "$f" ] && continue
  mkdir -p "$CTX/overlay/$(dirname "$f")"
  cp "$f" "$CTX/overlay/$f"
done < "$CTX/changed.txt"

STUDIO_STAGE_DOCKERFILE=""
STUDIO_COPY_DOCKERFILE=""
if [ "$HAS_STUDIO_CHANGES" -eq 1 ]; then
  if [ -f "web-studio/dist/index.html" ]; then
    echo "检测到 web-studio 改动，使用已有构建产物 web-studio/dist 叠加..."
    mkdir -p "$CTX/overlay/openviking/web_studio/dist"
    cp -r web-studio/dist/. "$CTX/overlay/openviking/web_studio/dist/"
    STUDIO_COPY_DOCKERFILE="RUN rm -rf $SITE_PACKAGES/openviking/web_studio/dist && mkdir -p $SITE_PACKAGES/openviking/web_studio/dist
COPY overlay/openviking/web_studio/dist/ $SITE_PACKAGES/openviking/web_studio/dist/"
  else
    echo "检测到 web-studio 改动，将在容器构建阶段编译 web-studio/dist..."
    mkdir -p "$CTX/web-studio"
    tar -cf - --exclude='node_modules' --exclude='dist' web-studio | tar -xf - -C "$CTX"
    NODE_IMAGE="${NODE_IMAGE:-node:24-trixie-slim}"
    STUDIO_STAGE_DOCKERFILE="FROM $NODE_IMAGE AS web-studio-builder
ARG TARGETPLATFORM
WORKDIR /app/web-studio
COPY web-studio/package.json web-studio/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm,id=npm-\${TARGETPLATFORM} npm ci
COPY web-studio/ ./
RUN npm run build -- --base=/studio/ && test -f dist/index.html
"
    STUDIO_COPY_DOCKERFILE="RUN rm -rf $SITE_PACKAGES/openviking/web_studio/dist
COPY --from=web-studio-builder /app/web-studio/dist/ $SITE_PACKAGES/openviking/web_studio/dist/"
  fi
fi

echo "将叠加 $(wc -l < "$CTX/changed.txt") 个 Python 文件$([ "$HAS_STUDIO_CHANGES" -eq 1 ] && printf ' + web-studio/dist' || true)到 $BASE_IMAGE:"
sed 's/^/  /' "$CTX/changed.txt"

cat > "$CTX/Dockerfile" <<EOF
${STUDIO_STAGE_DOCKERFILE}FROM $BASE_IMAGE
ARG SOURCE_COMMIT=unknown
# COPY 是合并语义，只覆盖同名文件；原生扩展、_version.py 等构建产物
# 不在 overlay 里，原样保留。COPY 保留源文件 mtime，与镜像内 .pyc 记录的
# mtime/size 不符，Python 会自动重编译，无需手工清理字节码。
COPY overlay/openviking/     $SITE_PACKAGES/openviking/
COPY overlay/openviking_cli/ $SITE_PACKAGES/openviking_cli/
${STUDIO_COPY_DOCKERFILE}
# 构建期自检：验证被替换的文件加载的确实是新代码，而非残留旧字节码。
RUN /app/.venv/bin/python -c "\\
from openviking.server.request_guard import run_guarded;\\
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS as M;\\
from openviking.server.config import ServerConfig;\\
import openviking.server.routers.search, openviking.storage.queuefs.semantic_processor;\\
from openviking.models.rerank.errors import RerankError;\\
from openviking.models.rerank.executor import get_rerank_executor;\\
from openviking.models.rerank.base import RerankBase;\\
from openviking_cli.utils.config import RerankConfig;\\
assert M['CANCELLED'] == 499, 'stale bytecode: models.py';\\
assert hasattr(ServerConfig(), 'request_timeout_seconds'), 'stale bytecode: config.py';\\
assert hasattr(RerankConfig(), 'max_retries'), 'stale bytecode: rerank_config.py';\\
assert hasattr(RerankBase, '_rerank_once'), 'stale bytecode: rerank/base.py';\\
print('overlay verified: new code is live')"
LABEL org.opencontainers.image.base.name="$BASE_IMAGE" \\
      org.opencontainers.image.revision="\$SOURCE_COMMIT"
ENV OPENVIKING_SOURCE_COMMIT=\$SOURCE_COMMIT
EOF

env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
  docker build --build-arg SOURCE_COMMIT="$COMMIT" -t "$IMAGE" "$CTX"

echo
echo "已构建 $IMAGE (source commit $COMMIT)"
echo "注意：该镜像已内含 B9/B10 修复，切换过去时必须从 compose 中移除 entrypoint"
echo "      补丁包装，否则 apply_*_patch.py 会因锚点消失 exit 1 拒绝启动。"
