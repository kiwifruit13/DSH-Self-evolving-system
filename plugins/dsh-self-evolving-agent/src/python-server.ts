import { spawn } from 'node:child_process'
import { isWriteMethod, type RpcMethod } from '@kiwifruit/dsh-self-evolving-contract'

export interface PythonServerConfig {
  pythonBin: string
  serveScript: string
  dbPath: string
  reconnectIntervalMs: number
  rpcTimeoutMs: number
  /** 只读模式：拒绝所有写方法（透传 --readonly） */
  readonly?: boolean
  /** 写操作鉴权 token（透传 --token，写方法需携带 auth） */
  token?: string
}

export interface RpcRequest {
  jsonrpc: '2.0'
  id: number
  method: string
  params: Record<string, unknown>
}

export interface RpcResponse {
  jsonrpc: '2.0'
  id: number
  result?: unknown
  error?: { code: number; message: string }
}

/** 写方法判定 —— 由契约 `@kiwifruit/dsh-self-evolving-contract` 单一真源定义
 *
 * 修复 BUG-35 复现地：原硬编码 5 个写方法名字面量，与 serve.py 的
 * `_WRITE_METHODS` 各写一份、靠注释维系，漏一处即所有写操作被服务端拒绝。
 * 现 TS 侧直接从契约 derive，serve.py 由 contract.spec.ts 一致性测试兜底双向对齐。
 */
function isWrite(method: string): boolean {
  return isWriteMethod(method as RpcMethod)
}

export class PythonServer {
  private config: PythonServerConfig
  private process: ReturnType<typeof spawn> | null = null
  private pending: Map<
    number,
    { resolve: (v: unknown) => void; reject: (e: Error) => void }
  > = new Map()
  private seq = 0
  private started = false
  private readyPromise: Promise<void> | null = null
  private readyResolve: (() => void) | null = null
  /** BUG-29 修复：ready 超时定时器引用，用于 clearTimeout */
  private readyTimer: ReturnType<typeof setTimeout> | null = null
  /** BUG-27 修复：重连定时器引用，用于去重 */
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

  constructor(config: PythonServerConfig) {
    this.config = config
  }

  get ready(): Promise<void> {
    if (!this.started) {
      throw new Error('PythonServer not started')
    }
    if (!this.readyPromise) {
      this.readyPromise = new Promise<void>((resolve, reject) => {
        this.readyResolve = resolve
        // BUG-29 修复：保存定时器引用，超时后清除
        this.readyTimer = setTimeout(() => {
          this.readyTimer = null
          this.readyResolve = null
          this.readyPromise = null
          reject(new Error('Python server not ready within 10s'))
        }, 10000)
      })
    }
    return this.readyPromise
  }

  start(): void {
    // BUG-26 修复：start 重入保护——先终止旧进程
    if (this.process) {
      this.process.kill('SIGTERM')
      this.process = null
    }
    // BUG-27 修复：清除旧的重连定时器
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    // BUG-47 补充：直接调用 start() 重启（而非经 startReconnect）时，
    // 旧进程遗留的 readyPromise 可能已兑现，导致新进程的 __ready__ 被
    // 视作迟到信号而跳过应有的等待语义。此处统一重置 ready 状态，
    // 强制下一次 get ready 重建 Promise 并挂载 10s 超时。
    this.readyPromise = null
    this.readyResolve = null
    if (this.readyTimer) {
      clearTimeout(this.readyTimer)
      this.readyTimer = null
    }
    this.started = true
    const args = [this.config.serveScript, this.config.dbPath]
    if (this.config.readonly) args.push('--readonly')
    if (this.config.token) args.push('--token', this.config.token)
    const proc = spawn(this.config.pythonBin, args, {
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    this.process = proc

    // BUG-46 修复：stdin 流级 error 监听。进程已死但 exit 未派发的窗口期
    // 写入 stdin 会触发 EPIPE/ERR_STREAM_DESTROYED——无监听器时作为
    // uncaught 'error' 事件直接崩溃宿主进程（BUG-25 只覆盖了 proc 级）。
    proc.stdin?.on('error', (err: Error) => {
      console.error('[python-server] stdin error:', err.message)
    })

    // BUG-25 修复：监听 error 事件，避免未处理错误崩溃宿主进程
    proc.on('error', (err: Error) => {
      console.error('[python-server] spawn error:', err.message)
      if (this.started) {
        this.startReconnect()
      }
    })

    let buffer = ''

    proc.stdout.on('data', (chunk: Buffer) => {
      buffer += chunk.toString()
      let newlineIdx = buffer.indexOf('\n')
      while (newlineIdx >= 0) {
        const line = buffer.slice(0, newlineIdx).replace(/\r$/, '')
        buffer = buffer.slice(newlineIdx + 1)

        if (line === '__ready__') {
          const resolve = this.readyResolve
          this.readyResolve = null
          // BUG-29 修复：ready 成功后清除超时定时器
          if (this.readyTimer) {
            clearTimeout(this.readyTimer)
            this.readyTimer = null
          }
          if (resolve) {
            resolve()
          } else {
            // BUG-47 修复：迟到唤醒。__ready__ 在 10s 超时之后才到达时，
            // readyResolve 已被超时回调清空，此前该信号被 no-op 消费且
            // serve.py 只发送一次 ⇒ 此后每次 call() 都等一个永不到来的
            // 信号，进程健康但所有 RPC 永久超时。此处把 ready 置为已兑现，
            // 后续 call() 立即放行。
            this.readyPromise = Promise.resolve()
          }
          return
        }

        try {
          const resp = JSON.parse(line) as RpcResponse
          const pending = this.pending.get(resp.id)
          this.pending.delete(resp.id)
          if (resp.error) {
            pending?.reject(new Error(resp.error.message))
          } else {
            pending?.resolve(resp.result)
          }
        } catch {
          // ignore malformed
        }

        newlineIdx = buffer.indexOf('\n')
      }
    })

    proc.stderr.on('data', (chunk: Buffer) => {
      console.error('[python-server]', chunk.toString())
    })

    proc.on('exit', (code) => {
      this.process = null
      // BUG-28 修复：进程退出时立即拒绝所有 pending 请求
      if (this.pending.size > 0) {
        this.pending.forEach(({ reject }) =>
          reject(new Error(`Python process died (exit code ${code})`)),
        )
        this.pending.clear()
      }
      if (this.started) {
        console.error('[python-server] exited with code', code)
        this.startReconnect()
      }
    })
  }

  stop(): void {
    this.started = false
    // BUG-27 修复：清除重连定时器
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    // BUG-29 修复：清除 ready 超时定时器
    if (this.readyTimer) {
      clearTimeout(this.readyTimer)
      this.readyTimer = null
    }
    this.process?.kill('SIGTERM')
    this.process = null
  }

  /** Kill Python 子进程并拒绝所有 pending 请求（信号中止时使用） */
  killProcess(): void {
    this.pending.forEach(({ reject }) =>
      reject(new Error('Python process killed')),
    )
    this.pending.clear()
    this.process?.kill('SIGTERM')
    this.process = null
  }

  private startReconnect(): void {
    if (!this.started) return
    // BUG-27 修复：去重——如果已有重连定时器，不再创建新的
    if (this.reconnectTimer) return
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.readyPromise = null
      this.readyResolve = null
      this.start()
    }, this.config.reconnectIntervalMs)
  }

  private send(req: RpcRequest): void {
    const stream = this.process?.stdin
    const item = this.pending.get(req.id)
    if (!stream || !this.process) {
      // 子进程未就绪/已死：拒绝该请求而非静默吞掉
      if (item) {
        this.pending.delete(req.id)
        item.reject(new Error('RPC send failed: Python process not running'))
      }
      return
    }
    stream.write(JSON.stringify(req) + '\n')
  }

  /**
   * RPC 调用（P0-1: exec.signal 转发）
   *
   * 约束：必须遵从 exec.signal（强制性）
   * - signal 触发 abort → kill Python 子进程 + Promise reject
   * - signal 已 abort → 直接 reject
   */
  async call(
    method: string,
    params: Record<string, unknown> = {},
    signal?: AbortSignal,
  ): Promise<unknown> {
    await this.ready

    // signal 已触发 → 立即拒绝
    if (signal?.aborted) {
      throw new Error('Aborted before RPC call')
    }

    const id = ++this.seq
    // BUG-35 修复：写方法统一注入 auth。此前 token 只作为 --token 传给
    // serve.py 启动参数，TS 层从不携带 auth ⇒ 配置 token 后所有写操作
    // 100% 被 _authorize 拒绝（配置承诺"写方法需携带 auth"无任何发送方）。
    const effectiveParams: Record<string, unknown> =
      this.config.token && isWrite(method)
        ? { ...params, auth: this.config.token }
        : params
    const req: RpcRequest = {
      jsonrpc: '2.0',
      id,
      method,
      params: effectiveParams,
    }

    const promise = new Promise<unknown>((resolve, reject) => {
      const timeoutMs = this.config.rpcTimeoutMs ?? 30000
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`RPC timeout: ${method}`))
      }, timeoutMs)

      this.pending.set(id, {
        resolve: (v) => {
          clearTimeout(timer)
          signal?.removeEventListener('abort', onAbort)
          resolve(v)
        },
        reject: (e) => {
          clearTimeout(timer)
          signal?.removeEventListener('abort', onAbort)
          reject(e)
        },
      })

      // P0-1: 注册 signal 中止监听
      const onAbort = () => {
        clearTimeout(timer)
        this.pending.delete(id)
        this.killProcess()
        reject(new Error('Aborted by caller'))
      }
      signal?.addEventListener('abort', onAbort, { once: true })
    })

    this.send(req)
    return promise
  }
}