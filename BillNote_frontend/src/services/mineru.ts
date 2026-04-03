import request from '@/utils/request'
import toast from 'react-hot-toast'

const MINERU_API_BASE = '/mineru'

export interface MinerUParseResponse {
  task_id: string
  file_name: string
  status: string
}

export interface MinerUQualityReport {
  total_chars: number
  heading_count: number
  h1_count: number
  h2_count: number
  h3_count: number
  h4_plus_count: number
  max_heading_depth: number
  has_valid_structure: boolean
  estimated_chapter_count: number
  warnings: string[]
  heading_hierarchy_valid: boolean
  heading_hierarchy_violations: string[]
  content_list_aligned: boolean
  content_list_warnings: string[]
}

export interface MinerUResult {
  md_content: string
  page_count: number
  image_count: number
  images: Record<string, string>
  parsing_time_ms: number
  quality_report: MinerUQualityReport
}

export interface MinerUTaskStatus {
  task_id: string
  status: string
  message: string
  result?: MinerUResult
}

/**
 * Upload PDF to MinerU for parsing
 * @param file - PDF file to upload
 * @param baseUrl - MinerU service base URL (e.g., http://localhost:8080)
 */
export const uploadDocument = async (
  file: File,
  baseUrl: string = 'https://mineru.net/api/v4/extract/',
  apiKey?: string,
): Promise<MinerUParseResponse> => {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('base_url', baseUrl)
  if (apiKey) {
    formData.append('api_key', apiKey)
  }
  formData.append('backend', 'hybrid-auto-engine')
  formData.append('parse_method', 'auto')
  formData.append('formula_enable', 'true')
  formData.append('table_enable', 'true')

  try {
    const response = await request.post(`${MINERU_API_BASE}/parse`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    toast.success('文档上传成功，开始解析...')
    return response as unknown as MinerUParseResponse
  } catch (e: any) {
    toast.error('文档上传失败，请重试')
    throw e
  }
}

/**
 * Poll MinerU task status
 * 使用 fetch 直接调用，绕过 axios 拦截器，避免同一错误两次 toast
 * @param taskId - Task ID from uploadDocument response
 */
export const getMineruTaskStatus = async (taskId: string): Promise<MinerUTaskStatus> => {
  const baseURL = import.meta.env.VITE_API_BASE_URL || ''
  const url = `${baseURL}${MINERU_API_BASE}/task/${taskId}`
  const response = await fetch(url)

  // 尝试解析 JSON，失败时返回 null 而不是抛出解析错误
  let body: any = {}
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    try {
      body = await response.json()
    } catch {
      // JSON 解析失败，body 保持为空对象
    }
  } else {
    // 非 JSON 响应（如 HTML 错误页），读取文本用于调试
    const text = await response.text().catch(() => '')
    if (!response.ok) {
      const err: any = new Error(`HTTP ${response.status}: ${text.slice(0, 200)}`)
      err.data = { msg: `服务器返回非 JSON 响应 (${response.status})` }
      err.response = { status: response.status, data: {} }
      throw err
    }
  }

  if (!response.ok) {
    // 抛出统一错误格式，由 useTaskPolling catch 统一处理 toast
    const err: any = new Error(body.msg || body.message || `HTTP ${response.status}`)
    err.data = body
    err.response = { status: response.status, data: body }
    throw err
  }
  return body
}

/**
 * Check MinerU service health
 * @param baseUrl - MinerU service base URL
 * @param apiKey - Optional API key (required for cloud mode)
 */
export const checkMineruHealth = async (baseUrl: string, apiKey?: string) => {
  try {
    const params = new URLSearchParams({ base_url: baseUrl })
    if (apiKey) {
      params.append('api_key', apiKey)
    }
    const response = await request.get(`${MINERU_API_BASE}/health?${params.toString()}`)
    return response
  } catch (e: any) {
    throw e
  }
}

/**
 * Start MinerU parsing by uploading file to backend, which then:
 * 1. Saves file locally
 * 2. Uploads to OSS (if cloud mode)
 * 3. Submits to MinerU API
 * 4. Returns task_id for polling
 *
 * This is used in "生成笔记" flow for local_doc platform.
 * Unlike uploadDocument which does upload + parse in one step when file is selected,
 * this function is called explicitly when user clicks "生成笔记".
 *
 * @param file - PDF file to upload and parse
 * @param baseUrl - MinerU service base URL
 * @param apiKey - Optional API key
 */
export const startMinerUParse = async (
  file: File,
  baseUrl: string = 'https://mineru.net/api/v4/extract/',
  apiKey?: string,
): Promise<MinerUParseResponse> => {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('base_url', baseUrl)
  if (apiKey) {
    formData.append('api_key', apiKey)
  }
  formData.append('backend', 'hybrid-auto-engine')
  formData.append('parse_method', 'auto')
  formData.append('formula_enable', 'true')
  formData.append('table_enable', 'true')

  try {
    const response = await request.post(`${MINERU_API_BASE}/upload-parse`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    return response as unknown as MinerUParseResponse
  } catch (e: any) {
    toast.error('启动解析失败，请重试')
    throw e
  }
}
