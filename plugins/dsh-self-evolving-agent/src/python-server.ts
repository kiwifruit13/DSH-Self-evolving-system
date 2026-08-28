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
        const timer = setTimeout(() => {
          this.readyResolve = null
          reject(new Error('Python server not ready within 10s'))
          clearTimeout(timer)
        }, 10000)
      })
    }
    return this.readyPromise
  }

  start(): void {
    this.started = true
    const args = [this.config.serveScript, this.config.dbPath]
    if (this.config.readonly) args.push('--readonly')
    if (this.config.token) args.push('--token', this.config.token)
    const proc = spawn(this.config.pythonBin, args, {
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    this.process = proc

    let buffer = ''

    proc.stdout.on('data', (chunk: Buffer) => {
      buffer += chunk.toString()
      let newlineIdx = buffer.indexOf('\n')
      while (newlineIdx >= 0) {
        // Windows 管道下 Python print 会输出 \r\n，剥离末尾 \r 避免握手/JSON 解析失败
        const line = buffer.slice(0, newlineIdx).replace(/\r$/, '')
        buffer = buffer.slice(newlineIdx + 1)

        if (line === '__ready__') {
          const resolve = this.readyResolve
          this.readyResolve = null
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
      if (this.started) {
        console.error('[python-server] exited with code', code)
        this.startReconnect()
      }
    })
  }

  stop(): void {
    this.started = false
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
    setTimeout(() => {
      this.readyPromise = null
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