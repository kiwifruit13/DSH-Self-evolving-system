import { spawn } from 'node:child_process'

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
    this.started = true
    const args = [this.config.serveScript, this.config.dbPath]
    if (this.config.readonly) args.push('--readonly')
    if (this.config.token) args.push('--token', this.config.token)
    const proc = spawn(this.config.pythonBin, args, {
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    this.process = proc

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
          resolve?.()
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
    const req: RpcRequest = {
      jsonrpc: '2.0',
      id,
      method,
      params,
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