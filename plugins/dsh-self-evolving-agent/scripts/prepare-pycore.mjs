// 发布前准备脚本：把项目根 Python 核心的 .py 源码复制为包内 pycore/src/，
// 使 npm 包「自包含」Python 核心（serve.py 可脱离项目仓库独立运行）。
// 真源始终是项目根 src/，本脚本生成的 pycore/ 仅为发布快照（被 .gitignore 忽略）。
//
// 安全红线（私人文件）：只复制以 .py 结尾的源码文件。严禁整目录 cp——根 src/ 可能
// 混有内部约束（如 src/CLAUDE.md、各种 .md 说明），一旦被 cpSync 带入 npm 包即泄密。
import { copyFileSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url)) // <pkg>/scripts
const pkgDir = path.resolve(here, '..') // 插件包目录
// <pkg>/scripts/../.. = 项目根
const rootSrc = path.resolve(pkgDir, '../../src')
const out = path.join(pkgDir, 'pycore', 'src')

rmSync(out, { recursive: true, force: true })
mkdirSync(out, { recursive: true })

let copied = 0
for (const name of readdirSync(rootSrc)) {
  const src = path.join(rootSrc, name)
  // 只收平铺的 .py 源；跳过子目录、非 .py 文件（含 CLAUDE.md 等私人/非代码文件）
  if (!statSync(src).isFile()) continue
  if (!name.endsWith('.py')) continue
  copyFileSync(src, path.join(out, name))
  copied += 1
}

if (copied === 0) {
  throw new Error(`[prepare-pycore] 未在 ${rootSrc} 找到任何 .py 源，发布中止`)
}
console.log(`[prepare-pycore] ${rootSrc} -> ${out} (${copied} 个 .py)`)