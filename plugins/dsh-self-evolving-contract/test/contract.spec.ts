/** 契约一致性守卫 —— 本包存在的核心理由
 *
 * 目标：让「改一侧漏另一侧」从「运行时才炸」变成「测试立刻红」。
 *
 * 覆盖三条防线：
 *   ① TS 类型层 ↔ contract.json（防 TS 侧漏改）
 *   ② contract.json ↔ 插件版 serve.py（防 Python 生产副本漏改）
 *   ③ 插件版 ↔ 根版 serve.py（防开发副本漏改，含错误码分支）
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
const DEV_SERVE = path.resolve(HERE, '../../../scripts/serve.py')

const pluginServeSrc = readFileSync(PLUGIN_SERVE, 'utf8')
const devServeSrc = readFileSync(DEV_SERVE, 'utf8')

/** 从 Python 源码中抽取 `NAME = frozenset({...})` 的字符串元素 */
function extractFrozenSet(src: string, name: string): Set<string> {
  const re = new RegExp(`${name}\\s*=\\s*frozenset\\(\\{([\\s\\S]*?)\\}\\)`)
  const m = src.match(re)
  if (!m) throw new Error(`serve.py 中未找到 ${name}`)
  return new Set([...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]))
}

/** 抽取 Python 源码中所有 `DomainError("CODE"` 的发射点 */
function extractDomainErrorEmissions(src: string): Set<string> {
  return new Set(
    [...src.matchAll(/DomainError\(\s*"([A-Z_]+)"/g)].map((m) => m[1]),
  )
}

/** 求值 `MAX_LINE_BYTES = <expr>`。
 *
 * Python 侧为可读性写作 `1 << 20`，契约里存的是计算后的 1048576。
 * 这里支持纯数字与左移表达式，避免为了迁就测试而牺牲源码可读性。
 */
function evalMaxLineBytes(src: string): number {
  const m = src.match(/MAX_LINE_BYTES\s*=\s*([^\n#]+)/)
  if (!m) throw new Error('serve.py 中未找到 MAX_LINE_BYTES')
  const expr = m[1].trim()
  const shift = expr.match(/^(\d+)\s*<<\s*(\d+)$/)
  const value = shift
    ? Number(shift[1]) << Number(shift[2])
    : Number(expr)
  if (!Number.isFinite(value)) {
    throw new Error(`MAX_LINE_BYTES 表达式无法求值: ${expr}`)
  }
  return value
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

describe('② contract.json ↔ 插件版 serve.py（生产副本）', () => {
  it('_ALLOWED_METHODS 与契约方法集合完全相等', () => {
    expect(sorted(extractFrozenSet(pluginServeSrc, '_ALLOWED_METHODS'))).toEqual(
      sorted(jsonMethods),
    )
  })

  it('_WRITE_METHODS 与契约写方法集合完全相等', () => {
    expect(sorted(extractFrozenSet(pluginServeSrc, '_WRITE_METHODS'))).toEqual(
      sorted(jsonWrite),
    )
  })

  it('_READ_METHODS 与契约读方法集合完全相等', () => {
    expect(sorted(extractFrozenSet(pluginServeSrc, '_READ_METHODS'))).toEqual(
      sorted(jsonRead),
    )
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

  it('握手信号与鉴权参数名与契约一致', () => {
    expect(pluginServeSrc).toContain(`"${contract.transport.readySignal}"`)
    expect(pluginServeSrc).toContain(`params.get("${contract.transport.authParam}")`)
  })

  it('MAX_LINE_BYTES 与契约一致', () => {
    expect(evalMaxLineBytes(pluginServeSrc)).toBe(contract.transport.maxLineBytes)
  })
})

describe('③ 插件版 ↔ 根版 serve.py（开发副本）', () => {
  it('两副本的 _ALLOWED_METHODS 一致', () => {
    expect(sorted(extractFrozenSet(devServeSrc, '_ALLOWED_METHODS'))).toEqual(
      sorted(extractFrozenSet(pluginServeSrc, '_ALLOWED_METHODS')),
    )
  })

  it('两副本的 _WRITE_METHODS 一致', () => {
    expect(sorted(extractFrozenSet(devServeSrc, '_WRITE_METHODS'))).toEqual(
      sorted(extractFrozenSet(pluginServeSrc, '_WRITE_METHODS')),
    )
  })

  it('两副本的 _READ_METHODS 一致', () => {
    expect(sorted(extractFrozenSet(devServeSrc, '_READ_METHODS'))).toEqual(
      sorted(extractFrozenSet(pluginServeSrc, '_READ_METHODS')),
    )
  })

  it('两副本的 DomainError 发射点集合一致', () => {
    expect(sorted(extractDomainErrorEmissions(devServeSrc))).toEqual(
      sorted(extractDomainErrorEmissions(pluginServeSrc)),
    )
  })

  it('两副本的 MAX_LINE_BYTES 一致', () => {
    expect(evalMaxLineBytes(devServeSrc)).toBe(evalMaxLineBytes(pluginServeSrc))
  })
})
