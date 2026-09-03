#!/bin/bash
# ============================================================
# 文件名：git-version.sh
# 功能：基于 Git Flow 节点自动生成语义化版本号 (SemVer 2.0)
# 用法：./git-version.sh  (输出纯版本号，供 CI 变量赋值)
# 说明：版本基线优先取自最新 Git Tag (vX.Y.Z)；若仓库尚无 Tag，
#       则回退到 plugins/dsh-self-evolving-agent/package.json 的 version 字段。
# ============================================================

set -e

# 脚本自身目录（用于 cwd 无关的 package.json 回退路径）
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# ---------- 1. 获取当前分支名 ----------
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null)
if [ -z "$BRANCH" ]; then
    echo "ERROR: 不在任何分支上（detached HEAD 状态），请切回分支" >&2
    exit 1
fi

# ---------- 2. 获取最新符合规范的 Tag（无则回退 package.json） ----------
LATEST_TAG=$(git describe --tags --match "v[0-9]*" --abbrev=0 2>/dev/null || true)
if [ -z "$LATEST_TAG" ]; then
    # 回退：读取插件 package.json 的 version 作为基线（路径以脚本位置为基准，cwd 无关）
    PKG_JSON="$SCRIPT_DIR/plugins/dsh-self-evolving-agent/package.json"
    if [ -f "$PKG_JSON" ]; then
        RAW=$(grep -m1 '"version"' "$PKG_JSON" | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
        LATEST_TAG="v${RAW}"
    else
        LATEST_TAG="v0.0.0"
    fi
fi

LATEST_VERSION=${LATEST_TAG#v}  # 去掉前缀 'v'

# 拆解出版基线 X.Y.Z（剥离可能的预发布后缀，如 -rc.1 / -SNAPSHOT）
BASE_VERSION=$(echo "$LATEST_VERSION" | sed -n 's/^\([0-9]*\)\.\([0-9]*\)\.\([0-9]*\).*/\1.\2.\3/p')
if [ -z "$BASE_VERSION" ]; then
    echo "WARN: 最新版本 ($LATEST_VERSION) 格式异常，重置为 0.0.0" >&2
    BASE_VERSION="0.0.0"
fi

IFS='.' read -r MAJOR MINOR PATCH <<< "$BASE_VERSION"

# ---------- 3. 核心逻辑：根据不同节点计算版本号 ----------
case "$BRANCH" in
    master|main)
        # 生产节点：直接返回基线纯净数字
        VERSION="$MAJOR.$MINOR.$PATCH"
        ;;

    develop)
        # 开发节点：次版本号+1，补丁归零，加 SNAPSHOT
        NEXT_MINOR=$((MINOR + 1))
        VERSION="$MAJOR.$NEXT_MINOR.0-SNAPSHOT"
        ;;

    release/*)
        # 发版节点：提取分支名中的版本 (如 release/v1.2.0 -> 1.2.0)
        RELEASE_VER=$(echo "$BRANCH" | sed -n 's/^release\/v\([0-9]*\.[0-9]*\.[0-9]*\)$/\1/p')
        if [ -z "$RELEASE_VER" ]; then
            echo "ERROR: release 分支命名必须为 release/vX.Y.Z (如 release/v1.2.0)" >&2
            exit 1
        fi
        # 自动递增 rc 计数（防止同版本多次构建覆盖）
        EXISTING_RC=$(git tag -l "v${RELEASE_VER}-rc.*" | wc -l)
        RC_NUM=$((EXISTING_RC + 1))
        VERSION="$RELEASE_VER-rc.$RC_NUM"
        ;;

    hotfix/*)
        # 热修复节点：补丁版本号+1，加 hotfix 后缀
        NEXT_PATCH=$((PATCH + 1))
        VERSION="$MAJOR.$MINOR.$NEXT_PATCH-hotfix"
        ;;

    *)
        # 其他未知分支（如 feature/*）：沿用 develop 逻辑，便于测试
        echo "WARN: 未知分支 '$BRANCH'，按 develop 规则处理（SNAPSHOT）" >&2
        NEXT_MINOR=$((MINOR + 1))
        VERSION="$MAJOR.$NEXT_MINOR.0-SNAPSHOT"
        ;;
esac

# ---------- 4. 输出最终版本号 ----------
echo "$VERSION"
