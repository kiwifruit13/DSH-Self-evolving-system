// 发布前准备脚本：把项目根 Python 核心 src/ 复制为包内 pycore/src/，
// 使 npm 包「自包含」Python 核心（serve.py 可脱离项目仓库独立运行）。
// 真源始终是项目根 src/，本脚本生成的 pycore/ 仅为发布快照（被 .gitignore 忽略）。
import { cpSync, mkdirSync, rmSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url)) // <pkg>/scripts
const pkgDir = path.resolve(here, '..') // 插件包目录
// <pkg>/scripts/../.. = 项目根
const rootSrc = path.resolve(pkgDir, '../../src')
const out = path.join(pkgDir, 'pycore', 'src')

rmSync(out, { recursive: true, force: true })
mkdirSync(path.dirname(out), { recursive: true })
cpSync(rootSrc, out, {
  recursive: true,
  filter: (s) => !s.includes('__pycache__') && !s.endsWith('.pyc'),
})
console.log(`[prepare-pycore] ${rootSrc} -> ${out}`)