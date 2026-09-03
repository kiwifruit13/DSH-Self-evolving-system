#!/usr/bin/env node
// 读取当前 Git 分支 -> 调用根目录 git-version.sh 计算版本 -> 写回插件 package.json 的 version 字段。
// 用法（在插件目录）：node scripts/set-version.mjs
//
// 该脚本是 GIT_WORKFLOW_TEMPLATE.md「第 7 章」npm 版落地：发布管理员切到正确分支后运行，
// 由 git-version.sh 推算版本并同步进 package.json，随后 npm publish。
import { execSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const pluginDir = __dirname;                       // plugins/dsh-self-evolving-agent/scripts
const rootDir = resolve(pluginDir, '..', '..', '..'); // 项目根（scripts -> 插件 -> plugins -> 根）
const pkgPath = resolve(pluginDir, '..', 'package.json');

const version = execSync('bash git-version.sh', { cwd: rootDir, encoding: 'utf8' }).trim();
if (!/^\d+\.\d+\.\d+/.test(version)) {
  console.error(`ERROR: git-version.sh 返回非法版本 "${version}"`);
  process.exit(1);
}

const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
pkg.version = version;
writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
console.log(`set version -> ${version}`);
