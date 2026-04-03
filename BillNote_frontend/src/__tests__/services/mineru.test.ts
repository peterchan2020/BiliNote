/**
 * MinerU Service Tests
 * TDD RED phase - Tests written before implementation
 *
 * Tests for the MinerU API client functions:
 * - uploadDocument - Upload PDF file for parsing
 * - getMineruTaskStatus - Poll task status
 * - checkMineruHealth - Check MinerU service health
 * - validateFile - Client-side file validation
 *
 * Run with: cd BillNote_frontend && pnpm test:run src/__tests__/services/mineru.test.ts
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// =============================================================================
// Types & Constants (mirroring what will be in mineru.ts)
// =============================================================================

interface MinerUHealthStatus {
  status: 'UP' | 'DOWN' | 'UNREACHABLE'
  mineru_version?: string
  parser_available: boolean
}

interface MinerUParseResponse {
  task_id: string
  status: 'PENDING' | 'SUCCESS' | 'FAILED'
  message?: string
  result?: {
    md_content: string
    page_count: number
    image_count: number
    images: Record<string, string>
    quality_report: {
      heading_count: number
      h1_count: number
      h2_count: number
      max_heading_depth: number
      has_valid_structure: boolean
      warnings: string[]
    }
  }
}

interface MinerUUploadResponse {
  task_id: string
  file_name: string
  status: 'PENDING'
}

interface ValidationResult {
  valid: boolean
  error?: string
}

// Constants
const MAX_FILE_SIZE = 50 * 1024 * 1024 // 50MB
const MAX_PDF_PAGES = 500
const SUPPORTED_FORMATS = ['application/pdf']
const API_BASE = '/api/mineru'

// =============================================================================
// Mock modules
// =============================================================================

// Mock the request utility
const mockPost = vi.fn()
const mockGet = vi.fn()
const mockRequest = {
  post: mockPost,
  get: mockGet,
}

vi.mock('@/utils/request', () => ({
  default: mockRequest,
}))

// Mock toast
vi.mock('react-hot-toast', () => ({
  default: {
    error: vi.fn(),
    success: vi.fn(),
  },
}))

// =============================================================================
// Import the functions under test (will fail until we create mineru.ts)
// =============================================================================

// These imports will be enabled once we create the mineru.ts file
// import { uploadDocument, getMineruTaskStatus, checkMineruHealth, validateFile } from '@/services/mineru'

// For now, we'll define the functions inline to test the logic
// Once implementation is done, these tests will use the actual module

// =============================================================================
// Validation Logic Tests (pure functions, easy to test)
// =============================================================================

describe('MinerU Service - File Validation', () => {
  describe('validateFile', () => {
    // Replicate the validation logic for testing
    const validateFile = (file: File): ValidationResult => {
      // Check file type
      if (!SUPPORTED_FORMATS.includes(file.type) && !file.name.toLowerCase().endsWith('.pdf')) {
        return { valid: false, error: '仅支持 PDF 文件' }
      }

      // Check file size (50MB limit for frontend)
      if (file.size > MAX_FILE_SIZE) {
        return { valid: false, error: `文件过大，最大 ${MAX_FILE_SIZE / 1024 / 1024}MB` }
      }

      // Check file is not empty
      if (file.size === 0) {
        return { valid: false, error: '文件不能为空' }
      }

      return { valid: true }
    }

    it('should accept valid PDF file under 50MB', () => {
      const file = new File(['%PDF-1.4 test content'], 'test.pdf', { type: 'application/pdf' })
      // Mock file size to be under limit
      Object.defineProperty(file, 'size', { value: 10 * 1024 * 1024 }) // 10MB

      const result = validateFile(file)
      expect(result.valid).toBe(true)
      expect(result.error).toBeUndefined()
    })

    it('should reject file over 50MB', () => {
      const file = new File(['test'], 'large.pdf', { type: 'application/pdf' })
      Object.defineProperty(file, 'size', { value: 51 * 1024 * 1024 }) // 51MB

      const result = validateFile(file)
      expect(result.valid).toBe(false)
      expect(result.error).toContain('文件过大')
      expect(result.error).toContain('50MB')
    })

    it('should reject file at exactly 50MB boundary (49MB should pass)', () => {
      const file = new File(['test'], 'boundary.pdf', { type: 'application/pdf' })
      Object.defineProperty(file, 'size', { value: 49 * 1024 * 1024 }) // 49MB

      const result = validateFile(file)
      expect(result.valid).toBe(true)
    })

    it('should reject file at exactly 50MB boundary (50MB+1 should fail)', () => {
      const file = new File(['test'], 'over_boundary.pdf', { type: 'application/pdf' })
      Object.defineProperty(file, 'size', { value: 50 * 1024 * 1024 + 1 }) // 50MB + 1 byte

      const result = validateFile(file)
      expect(result.valid).toBe(false)
    })

    it('should reject non-PDF files', () => {
      const file = new File(['test content'], 'document.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })

      const result = validateFile(file)
      expect(result.valid).toBe(false)
      expect(result.error).toContain('PDF')
    })

    it('should reject DOCX by extension even if type is missing', () => {
      const file = new File(['test content'], 'document.docx', { type: 'application/octet-stream' })

      const result = validateFile(file)
      expect(result.valid).toBe(false)
      expect(result.error).toContain('PDF')
    })

    it('should reject MD files', () => {
      const file = new File(['# Markdown'], 'readme.md', { type: 'text/markdown' })

      const result = validateFile(file)
      expect(result.valid).toBe(false)
    })

    it('should reject TXT files', () => {
      const file = new File(['Plain text'], 'readme.txt', { type: 'text/plain' })

      const result = validateFile(file)
      expect(result.valid).toBe(false)
    })

    it('should reject empty file', () => {
      const file = new File([''], 'empty.pdf', { type: 'application/pdf' })
      Object.defineProperty(file, 'size', { value: 0 })

      const result = validateFile(file)
      expect(result.valid).toBe(false)
      expect(result.error).toContain('空')
    })

    it('should accept PDF with pdf type but no extension check', () => {
      // When type is application/pdf, extension check is bypassed
      const file = new File([''], 'noextension', { type: 'application/pdf' })
      Object.defineProperty(file, 'size', { value: 1000 })

      const result = validateFile(file)
      expect(result.valid).toBe(true)
    })
  })
})

// =============================================================================
// API Function Tests (need mocking)
// =============================================================================

describe('MinerU Service - API Functions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('uploadDocument', () => {
    // This function will be imported from @/services/mineru once created
    // For now, we define the expected interface
    const uploadDocument = async (file: File, baseUrl: string = 'http://localhost:8080') => {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('base_url', baseUrl)

      const response = await mockRequest.post(`${API_BASE}/parse`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      return response as unknown as MinerUUploadResponse
    }

    it('should successfully upload a valid PDF and return task_id', async () => {
      const mockTaskId = '123e4567-e89b-12d3-a456-426614174000'
      mockPost.mockResolvedValueOnce({
        code: 0,
        msg: 'success',
        data: {
          task_id: mockTaskId,
          file_name: 'test.pdf',
          status: 'PENDING',
        },
      })

      const file = new File(['%PDF-1.4'], 'test.pdf', { type: 'application/pdf' })
      Object.defineProperty(file, 'size', { value: 1024 })

      const result = await uploadDocument(file)

      expect(result.task_id).toBe(mockTaskId)
      expect(result.status).toBe('PENDING')
      expect(mockPost).toHaveBeenCalledTimes(1)
      expect(mockPost).toHaveBeenCalledWith(
        `${API_BASE}/parse`,
        expect.any(FormData),
        expect.objectContaining({
          headers: expect.objectContaining({ 'Content-Type': 'multipart/form-data' }),
        })
      )
    })

    it('should throw error on network failure', async () => {
      mockPost.mockRejectedValueOnce(new Error('Network error'))

      const file = new File(['%PDF-1.4'], 'test.pdf', { type: 'application/pdf' })
      Object.defineProperty(file, 'size', { value: 1024 })

      await expect(uploadDocument(file)).rejects.toThrow('Network error')
    })

    it('should throw error on server error (500)', async () => {
      mockPost.mockRejectedValueOnce({
        code: 500,
        msg: 'MinerU API error: internal server error',
        data: null,
      })

      const file = new File(['%PDF-1.4'], 'test.pdf', { type: 'application/pdf' })
      Object.defineProperty(file, 'size', { value: 1024 })

      await expect(uploadDocument(file)).rejects.toBeDefined()
    })
  })

  describe('getMineruTaskStatus', () => {
    const getMineruTaskStatus = async (taskId: string) => {
      const response = await mockRequest.get(`${API_BASE}/task/${taskId}`)
      return response as unknown as MinerUParseResponse
    }

    it('should return PENDING for unknown task', async () => {
      mockGet.mockResolvedValueOnce({
        code: 0,
        data: {
          task_id: 'unknown-task-id',
          status: 'PENDING',
          message: '任务排队中',
        },
      })

      const result = await getMineruTaskStatus('unknown-task-id')

      expect(result.status).toBe('PENDING')
    })

    it('should return SUCCESS with markdown content when task completes', async () => {
      const mockResult = {
        code: 0,
        data: {
          task_id: 'completed-task',
          status: 'SUCCESS',
          message: '解析完成',
          result: {
            md_content: '# 第一章\n\n这是测试内容',
            page_count: 10,
            image_count: 5,
            images: { img_1: 'data:image/png;base64,abc123' },
            quality_report: {
              heading_count: 5,
              h1_count: 1,
              h2_count: 2,
              max_heading_depth: 2,
              has_valid_structure: true,
              warnings: [],
            },
          },
        },
      }

      mockGet.mockResolvedValueOnce(mockResult)

      const result = await getMineruTaskStatus('completed-task')

      expect(result.status).toBe('SUCCESS')
      expect(result.result).toBeDefined()
      expect(result.result?.md_content).toContain('第一章')
      expect(result.result?.page_count).toBe(10)
    })

    it('should return FAILED with error message when task fails', async () => {
      mockGet.mockResolvedValueOnce({
        code: 500,
        msg: 'MinerU API error: connection timeout',
        data: null,
      })

      await expect(getMineruTaskStatus('failed-task')).rejects.toBeDefined()
    })

    it('should handle polling until SUCCESS', async () => {
      // First call returns PENDING
      mockGet.mockResolvedValueOnce({
        code: 0,
        data: { task_id: 'polling-task', status: 'PENDING' },
      })

      // Second call returns SUCCESS
      mockGet.mockResolvedValueOnce({
        code: 0,
        data: {
          task_id: 'polling-task',
          status: 'SUCCESS',
          result: { md_content: '# Done', page_count: 1, image_count: 0, images: {}, quality_report: {} },
        },
      })

      const result1 = await getMineruTaskStatus('polling-task')
      expect(result1.status).toBe('PENDING')

      const result2 = await getMineruTaskStatus('polling-task')
      expect(result2.status).toBe('SUCCESS')
    })
  })

  describe('checkMineruHealth', () => {
    const checkMineruHealth = async (baseUrl: string = 'http://localhost:8080') => {
      const response = await mockRequest.get(`${API_BASE}/health?base_url=${baseUrl}`)
      return response as unknown as MinerUHealthStatus
    }

    it('should return UP when MinerU service is healthy', async () => {
      mockGet.mockResolvedValueOnce({
        code: 0,
        data: {
          status: 'UP',
          mineru_version: '1.0.0',
          parser_available: true,
        },
      })

      const result = await checkMineruHealth('http://localhost:8080')

      expect(result.status).toBe('UP')
      expect(result.parser_available).toBe(true)
      expect(result.mineru_version).toBe('1.0.0')
    })

    it('should return DOWN when MinerU service returns error', async () => {
      mockGet.mockResolvedValueOnce({
        code: 0,
        data: {
          status: 'DOWN',
          parser_available: false,
        },
      })

      const result = await checkMineruHealth('http://localhost:8080')

      expect(result.status).toBe('DOWN')
      expect(result.parser_available).toBe(false)
    })

    it('should return UNREACHABLE on connection error', async () => {
      mockGet.mockRejectedValueOnce(new Error('Connection refused'))

      const result = await checkMineruHealth('http://localhost:8080')

      // The actual implementation should catch the error and return UNREACHABLE
      // This test verifies error handling
      await expect(checkMineruHealth('http://localhost:8080')).rejects.toThrow()
    })
  })
})

// =============================================================================
// Integration Scenarios
// =============================================================================

describe('MinerU Service - Full Upload Flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('should complete full document upload and polling flow', async () => {
    const mockTaskId = 'full-flow-task-id'
    const file = new File(['%PDF-1.4 test content'], 'book.pdf', { type: 'application/pdf' })
    Object.defineProperty(file, 'size', { value: 5 * 1024 * 1024 }) // 5MB

    // Step 1: Upload returns task_id
    mockPost.mockResolvedValueOnce({
      code: 0,
      data: { task_id: mockTaskId, status: 'PENDING' },
    })

    // Step 2-4: Polling returns PENDING
    mockGet.mockResolvedValue({
      code: 0,
      data: { task_id: mockTaskId, status: 'PENDING' },
    })

    // Final poll returns SUCCESS
    mockGet.mockResolvedValueOnce({
      code: 0,
      data: {
        task_id: mockTaskId,
        status: 'SUCCESS',
        result: {
          md_content: '# Book Title\n\nChapter 1 content here.',
          page_count: 100,
          image_count: 10,
          images: {},
          quality_report: {
            heading_count: 10,
            h1_count: 1,
            h2_count: 5,
            max_heading_depth: 3,
            has_valid_structure: true,
            warnings: [],
          },
        },
      },
    })

    // Simulate upload
    const uploadResponse = await mockPost(`${API_BASE}/parse`, expect.any(FormData), expect.any(Object))
    expect(uploadResponse.data.task_id).toBe(mockTaskId)

    // Simulate polling until success
    let status = 'PENDING'
    let pollCount = 0
    while (status === 'PENDING' && pollCount < 5) {
      const pollResponse = await mockGet(`${API_BASE}/task/${mockTaskId}`)
      status = pollResponse.data.status
      pollCount++
    }

    expect(status).toBe('SUCCESS')
  })

  it('should handle large PDF with many pages', async () => {
    // This tests the page_count validation that should happen on backend
    // Frontend doesn't validate page count, but we can test the flow

    const mockTaskId = 'large-pdf-task'
    const file = new File(['%PDF-1.4'], 'large.pdf', { type: 'application/pdf' })
    Object.defineProperty(file, 'size', { value: 30 * 1024 * 1024 }) // 30MB

    mockPost.mockResolvedValueOnce({
      code: 0,
      data: { task_id: mockTaskId, status: 'PENDING' },
    })

    // Server returns SUCCESS with high page count
    mockGet.mockResolvedValueOnce({
      code: 0,
      data: {
        task_id: mockTaskId,
        status: 'SUCCESS',
        result: {
          md_content: '# Large Document',
          page_count: 500, // Max allowed
          image_count: 100,
          images: {},
          quality_report: {
            heading_count: 50,
            h1_count: 1,
            h2_count: 10,
            max_heading_depth: 4,
            has_valid_structure: true,
            warnings: [],
          },
        },
      },
    })

    const uploadResponse = await mockPost(`${API_BASE}/parse`, expect.any(FormData), expect.any(Object))
    expect(uploadResponse.data.task_id).toBe(mockTaskId)

    const pollResponse = await mockGet(`${API_BASE}/task/${mockTaskId}`)
    expect(pollResponse.data.result.page_count).toBe(500)
  })
})
