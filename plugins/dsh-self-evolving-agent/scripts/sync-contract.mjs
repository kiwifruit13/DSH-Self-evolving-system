// 把契约 TS 类型层内置进 agent（单一自包含 npm 包，不发布独立契约包）。
//
// 发布策略：`plugins/dsh-self-evolving-contract/` 仍是仓库内 contract.json 与 TS
// 类型层的单一真源（python 端 serve.py 的开发态加载路径也指向它），但 npm 发布物
// 只允许一个包 —— agent。因此 agent 在 build 前把契约 TS 原样复制进
// `src/contract/`，编译进 lib/ 后随包分发，无任何对外 npm 依赖。
//
// 红线：本脚本只整文件复制契约包的 .ts 源，禁止自行改写内容 —— 两侧必须字节一致，
// 否则 `--check`（prepack 阶段强制运行）会中止发布，防止漂移静默进入 npm 包。
import { copyFileSync, mkdirSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url)) // <pkg>/scripts
const pkgDir = path.resolve(here, '..') // 插件包目录
const srcDir = path.resolve(pkgDir, '../dsh-self-evolving-contract/src')
const outDir = path.join(pkgDir, 'src', 'contract')

const mode = process.argv[2] === '--check' ? 'check' : 'sync'

const sourceFiles = readdirSync(srcDir).filter((name) => name.endsWith('.ts'))

function sync() {
  rmSync(outDir, { recursive: true, force: true })
  mkdirSync(outDir, { recursive: true })
  for (const name of sourceFiles) {
    copyFileSync(path.join(srcDir, name), path.join(outDir, name))
  }
  console.log(`[sync-contract] ${srcDir} -> ${outDir} (${sourceFiles.length} 个 .ts)`)
}

function check() {
  const outFiles = readdirSync(outDir).filter((name) => name.endsWith('.ts'))
  if (outFiles.length !== sourceFiles.length) {
    throw new Error(
      `[sync-contract] 文件清单不一致（源 ${sourceFiles.length} / 内置 ${outFiles.length}）。` +
        '契约真源与 agent 内置副本已漂移，请运行 `node scripts/sync-contract.mjs` 后重新 build。',
    )
  }
  for (const name of sourceFiles) {
    const a = readFileSync(path.join(srcDir, name))
    const b = readFileSync(path.join(outDir, name))
    if (!a.equals(b)) {
      throw new Error(
        `[sync-contract] ${name} 与契约真源不一致，发布中止。` +
          '契约真源与 agent 内置副本已漂移，请运行 `node scripts/sync-contract.mjs` 后重新 build。',
      )
    }
  }
  console.log(`[sync-contract] 内置副本与契约真源一致 (${sourceFiles.length} 个 .ts)`)
}

if (mode === 'check') check()
else sync()
