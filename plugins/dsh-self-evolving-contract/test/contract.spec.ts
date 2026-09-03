/** 契约一致性守卫 —— 本包存在的核心理由
 *
 * 目标：让「改一侧漏另一侧」从「运行时才炸」变成「测试立刻红」。
 *
 * 覆盖三条防线：
 *   ① TS 类型层 ↔ contract.json（防 TS 侧漏改）
 *   ② contract.json ↔ serve.py 源码级派生（防 Python 侧绕过契约硬编码）
 *   ③ contract.json 真源 ↔ pycore bundle 快照（防发布包内契约过期）
 *
 * 防线 ② 已从「字面量比对」升级为「派生关系验证」：serve.py 不再硬编码任何
 * 契约值，改为启动时从 contract.json 派生（缺失即 fail-fast）。因此这里断言的
 * 是「派生关系存在且无字面量绕过」，而非「两处字面量相等」。
 *
 * 有此测试后，contract-todo.md 中的契约类 #1 #2 #3 #7 永久免疫。
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import {
  ALL_ERROR_CODES,
  AUTH_PARAM,
  DOMAIN_ERROR_CODES,
  INFRA_ERROR_CODE,
  JSONRPC_VERSION,
  MAX_LINE_BYTES,
  READ_METHODS,
  READY_SIGNAL,
  RPC_ERROR_CODES,
  RPC_METHODS,
  TOOL_TO_METHOD,
  WRITE_METHODS,
} from '../src/index.js'
import contract from '../contract.json'
import {
  AUTH_PARAM as JSON_AUTH_PARAM,
  JSONRPC_VERSION as JSON_JSONRPC,
  MAX_LINE_BYTES as JSON_MAX_LINE_BYTES,
  RPC_ERROR_CODES as JSON_RPC_ERROR_CODES,
  READY_SIGNAL as JSON_READY_SIGNAL,
} from '../src/transport.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PLUGIN_SERVE = path.resolve(
  HERE,
  '../../dsh-self-evolving-agent/scripts/serve.py',
)
/** 打包快照：prepare-pycore 把契约复制进 pycore/，与 Python 核心同生命周期 */
const BUNDLE_CONTRACT = path.resolve(
  HERE,
  '../../dsh-self-evolving-agent/pycore/contract.json',
)

const pluginServeSrc = readFileSync(PLUGIN_SERVE, 'utf8')

/** 抽取 Python 源码中所有 `DomainError("CODE"` 的发射点 */
function extractDomainErrorEmissions(src: string): Set<string> {
  return new Set(
    [...src.matchAll(/DomainError\(\s*"([A-Z_]+)"/g)].map((m) => m[1]),
  )
}

const jsonMethods = Object.keys(contract.methods)
const jsonWrite = jsonMethods.filter(
  (m) => contract.methods[m as keyof typeof contract.methods].kind === 'write',
)
const jsonRead = jsonMethods.filter(
  (m) => contract.methods[m as keyof typeof contract.methods].kind === 'read',
)
const jsonDomainErrors = Object.keys(contract.domainErrors)

function sorted(s: Iterable<string>): string[] {
  return [...s].sort()
}

describe('① TS 类型层 ↔ contract.json', () => {
  it('RPC_METHODS 与 contract.json 的 methods 键集合完全相等', () => {
    expect(sorted(RPC_METHODS)).toEqual(sorted(jsonMethods))
  })

  it('WRITE_METHODS 与 contract.json 中 kind=write 的集合完全相等', () => {
    expect(sorted(WRITE_METHODS)).toEqual(sorted(jsonWrite))
  })

  it('READ_METHODS 与 contract.json 中 kind=read 的集合完全相等', () => {
    expect(sorted(READ_METHODS)).toEqual(sorted(jsonRead))
  })

  it('读写分类互不重叠且恰好覆盖全部方法', () => {
    const all = new Set([...WRITE_METHODS, ...READ_METHODS])
    expect(all.size).toBe(jsonMethods.length)
    expect([...WRITE_METHODS].filter((m) => READ_METHODS.has(m))).toEqual([])
  })

  it('DOMAIN_ERROR_CODES 与 contract.json 的 domainErrors 键集合完全相等', () => {
    expect(sorted(DOMAIN_ERROR_CODES)).toEqual(sorted(jsonDomainErrors))
  })

  it('ALL_ERROR_CODES 恰好等于领域码 + INFRA', () => {
    expect(sorted(ALL_ERROR_CODES)).toEqual(
      sorted(new Set([...jsonDomainErrors, INFRA_ERROR_CODE])),
    )
    // contract.json 的 infraError 必须与 TS 侧常量同名，否则两侧语义分裂
    expect(contract.infraError).toBe(INFRA_ERROR_CODE)
  })

  it('TOOL_TO_METHOD 的每个目标方法都在 RPC_METHODS 内', () => {
    for (const method of Object.values(TOOL_TO_METHOD)) {
      expect(RPC_METHODS).toContain(method)
    }
  })

  it('每个有 tool 映射的方法，其 tool 名与 contract.json 一致', () => {
    for (const [tool, method] of Object.entries(TOOL_TO_METHOD)) {
      const def = contract.methods[method]
      expect(def.tool, `方法 ${method} 的工具名`).toBe(tool)
    }
  })

  it('contract.json 中标注了 tool 的方法，全部被 TOOL_TO_METHOD 覆盖', () => {
    const expected = jsonMethods.filter(
      (m) => contract.methods[m as keyof typeof contract.methods].tool !== null,
    )
    expect(sorted(Object.values(TOOL_TO_METHOD))).toEqual(sorted(expected))
  })

  it('传输常量与 contract.json 完全一致', () => {
    expect(READY_SIGNAL).toBe(JSON_READY_SIGNAL)
    expect(JSONRPC_VERSION).toBe(JSON_JSONRPC)
    expect(AUTH_PARAM).toBe(JSON_AUTH_PARAM)
    expect(MAX_LINE_BYTES).toBe(JSON_MAX_LINE_BYTES)
    expect(READY_SIGNAL).toBe(contract.transport.readySignal)
    expect(JSONRPC_VERSION).toBe(contract.transport.jsonrpcVersion)
    expect(AUTH_PARAM).toBe(contract.transport.authParam)
    expect(MAX_LINE_BYTES).toBe(contract.transport.maxLineBytes)
  })

  it('JSON-RPC 错误码与 contract.json 完全一致', () => {
    expect({ ...RPC_ERROR_CODES }).toEqual({ ...JSON_RPC_ERROR_CODES })
    expect({ ...RPC_ERROR_CODES }).toEqual({ ...contract.transport.rpcErrorCodes })
  })
})

describe('② contract.json ↔ serve.py（源码级派生）', () => {
  it('从 contract.json 加载契约，而非硬编码', () => {
    expect(pluginServeSrc).toMatch(/def _load_contract\s*\(/)
    expect(pluginServeSrc).toContain('_CONTRACT: dict[str, Any] = _load_contract()')
  })

  it('方法白名单与读写分类均从契约派生', () => {
    expect(pluginServeSrc).toContain(
      '_ALLOWED_METHODS = frozenset(_CONTRACT["methods"])',
    )
    expect(pluginServeSrc).toMatch(/_READ_METHODS\s*=\s*_methods_of_kind\("read"\)/)
    expect(pluginServeSrc).toMatch(/_WRITE_METHODS\s*=\s*_methods_of_kind\("write"\)/)
  })

  it('传输常量与 RPC 错误码均从契约派生', () => {
    expect(pluginServeSrc).toContain(
      'MAX_LINE_BYTES = int(_TRANSPORT["maxLineBytes"])',
    )
    expect(pluginServeSrc).toMatch(
      /_JSONRPC_VERSION\s*=\s*str\(_TRANSPORT\["jsonrpcVersion"\]\)/,
    )
    expect(pluginServeSrc).toMatch(
      /_READY_SIGNAL\s*=\s*str\(_TRANSPORT\["readySignal"\]\)/,
    )
    expect(pluginServeSrc).toMatch(
      /_AUTH_PARAM\s*=\s*str\(_TRANSPORT\["authParam"\]\)/,
    )
    for (const key of Object.keys(contract.transport.rpcErrorCodes)) {
      expect(
        pluginServeSrc,
        `RPC 错误码 ${key} 未从契约派生`,
      ).toContain(`_RPC_ERROR_CODES["${key}"]`)
    }
  })

  it('领域错误码全集从契约派生，且未知码构造即失败', () => {
    expect(pluginServeSrc).toContain(
      'DOMAIN_ERROR_CODES = frozenset(_CONTRACT["domainErrors"])',
    )
    // 防止有人绕过契约凭空造码 —— BUG-36 的根因正是领域码与 TS 侧白名单不一致
    expect(pluginServeSrc).toMatch(
      /if code not in DOMAIN_ERROR_CODES:\s*\n\s*raise RuntimeError/,
    )
  })

  it('零残留：serve.py 中不出现任何契约值的硬编码字面量', () => {
    for (const m of jsonMethods) {
      expect(pluginServeSrc, `serve.py 硬编码了方法名 ${m}`).not.toContain(
        `"${m}"`,
      )
    }
    for (const code of Object.values(contract.transport.rpcErrorCodes)) {
      expect(
        pluginServeSrc,
        `serve.py 硬编码了 RPC 错误码 ${code}`,
      ).not.toContain(`${code},`)
    }
    expect(pluginServeSrc).not.toContain(`"${contract.transport.jsonrpcVersion}"`)
    expect(pluginServeSrc).not.toContain(`"${contract.transport.readySignal}"`)
    expect(pluginServeSrc).not.toContain(`params.get("${contract.transport.authParam}")`)
  })

  it('契约缺失时 fail-fast，不静默降级', () => {
    expect(pluginServeSrc).toMatch(/raise RuntimeError\(\s*"未找到契约文件/)
  })

  it('所有 DomainError 发射点都在契约的领域错误码内', () => {
    const emitted = extractDomainErrorEmissions(pluginServeSrc)
    expect(emitted.size).toBeGreaterThan(0)
    for (const code of emitted) {
      expect(
        DOMAIN_ERROR_CODES.has(code as never),
        `serve.py 抛出了契约未声明的领域错误码 ${code}`,
      ).toBe(true)
    }
  })

  it('契约声明的领域错误码全部有实际发射点（无死码）', () => {
    const emitted = extractDomainErrorEmissions(pluginServeSrc)
    for (const code of DOMAIN_ERROR_CODES) {
      expect(emitted.has(code), `契约错误码 ${code} 在 serve.py 中无发射点`).toBe(
        true,
      )
    }
  })
})

describe('③ contract.json 真源 ↔ pycore bundle 快照', () => {
  it('打包快照存在且与真源字节级一致', () => {
    const bundle = readFileSync(BUNDLE_CONTRACT, 'utf8')
    const source = readFileSync(path.resolve(HERE, '../contract.json'), 'utf8')
    expect(
      bundle,
      'pycore/contract.json 与契约真源不一致 —— 请执行 `npm run prepack` 重建',
    ).toBe(source)
  })

  it('打包快照可解析，方法/错误码/限长与真源一致', () => {
    const bundle = JSON.parse(readFileSync(BUNDLE_CONTRACT, 'utf8'))
    expect(sorted(Object.keys(bundle.methods))).toEqual(sorted(jsonMethods))
    expect(sorted(Object.keys(bundle.domainErrors))).toEqual(
      sorted(jsonDomainErrors),
    )
    expect(bundle.transport.maxLineBytes).toBe(contract.transport.maxLineBytes)
  })
})
