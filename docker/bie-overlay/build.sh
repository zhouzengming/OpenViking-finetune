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
  | grep -v '^tests/' > "$CTX/changed.txt"
if [ ! -s "$CTX/changed.txt" ]; then
  echo "ERROR: 相对 $BASE_TAG 没有需要叠加的包内源码改动。" >&2
  exit 1
fi
while read -r f; do
  mkdir -p "$CTX/overlay/$(dirname "$f")"
  cp "$f" "$CTX/overlay/$f"
done < "$CTX/changed.txt"

echo "将叠加 $(wc -l < "$CTX/changed.txt") 个文件到 $BASE_IMAGE:"
sed 's/^/  /' "$CTX/changed.txt"

cat > "$CTX/Dockerfile" <<EOF
FROM $BASE_IMAGE
ARG SOURCE_COMMIT=unknown
# COPY 是合并语义，只覆盖同名文件；原生扩展、web_studio、_version.py 等构建产物
# 不在 overlay 里，原样保留。COPY 保留源文件 mtime，与镜像内 .pyc 记录的
# mtime/size 不符，Python 会自动重编译，无需手工清理字节码。
COPY overlay/openviking/     $SITE_PACKAGES/openviking/
COPY overlay/openviking_cli/ $SITE_PACKAGES/openviking_cli/
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
