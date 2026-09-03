#!/usr/bin/env node
// 读取当前 Git 分支 -> 调用根目录 git-version.sh 计算版本 -> 同步进：
//   1) 插件 package.json 的 version 字段（保留完整计算版本，含 -rc / -SNAPSHOT，供 npm dist-tag）
//   2) 两处 README 安装命令里的版本钉（@kiwifruit/dsh-self-evolving-agent@X.Y.Z），仅展示稳定 X.Y.Z
// 用法（在插件目录）：node scripts/set-version.mjs
//
// 该脚本是 GIT_WORKFLOW_TEMPLATE.md「第 7 章」npm 版落地：发布管理员切到正确分支后运行一次，
// 由 git-version.sh 推算版本并同步进 package.json + README，随后 npm publish。版本号单源，
// 不再需要手动改 README。
import { execSync } from 'node:child_process';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const pluginDir = __dirname;                           // plugins/dsh-self-evolving-agent/scripts
const rootDir = resolve(pluginDir, '..', '..', '..'); // 项目根（scripts -> 插件 -> plugins -> 根）
const pkgPath = resolve(pluginDir, '..', 'package.json');
const pluginReadme = resolve(pluginDir, '..', 'README.md');
const rootReadme = resolve(rootDir, 'README.md');

const version = execSync('bash git-version.sh', { cwd: rootDir, encoding: 'utf8' }).trim();
if (!/^\d+\.\d+\.\d+/.test(version)) {
  console.error(`ERROR: git-version.sh 返回非法版本 "${version}"`);
  process.exit(1);
}

// 文档安装命令只展示稳定 X.Y.Z（避免把 -rc.N / -SNAPSHOT 暴露给最终用户）；
// package.json 保留完整版本，npm publish 据此决定正式/预发标签。
const readmeVersion = version.split('-')[0];

// ---- 1) package.json ----
const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
pkg.version = version;
writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
console.log(`set version -> ${version}`);

// ---- 2) README 安装命令钉版本 ----
// 匹配 `@kiwifruit/dsh-self-evolving-agent@<版本>`，仅替换版本段。
const PIN_RE = /(@kiwifruit\/dsh-self-evolving-agent@)\d+\.\d+\.\d+(?:-[\w.]+)?/;

function syncReadme(path, ver) {
  if (!existsSync(path)) return;
  const txt = readFileSync(path, 'utf8');
  if (!PIN_RE.test(txt)) return; // 无版本钉，跳过（不强行注入）
  const next = txt.replace(new RegExp(PIN_RE.source, 'g'), `$1${ver}`);
  if (next !== txt) {
    writeFileSync(path, next);
    console.log(`set README version -> ${ver} (${path})`);
  }
}

syncReadme(pluginReadme, readmeVersion);
syncReadme(rootReadme, readmeVersion);
