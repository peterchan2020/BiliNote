import { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Input } from '@/components/ui/input'
import {
  FileText,
  Loader2,
  Save,
  Activity,
  CheckCircle2,
  XCircle,
  Eye,
  EyeOff,
} from 'lucide-react'
import { toast } from 'react-hot-toast'
import { useMinerUStore } from '@/store/mineruStore'
import { checkMineruHealth } from '@/services/mineru'

export default function Mineru() {
  const { baseUrl, apiKey, setBaseUrl, setApiKey } = useMinerUStore()
  const [inputUrl, setInputUrl] = useState(baseUrl)
  const [inputApiKey, setInputApiKey] = useState(apiKey)
  const [showApiKey, setShowApiKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [checking, setChecking] = useState(false)
  const [healthStatus, setHealthStatus] = useState<'unknown' | 'ok' | 'error'>('unknown')
  const [healthMessage, setHealthMessage] = useState('')

  // 同步 baseUrl 变化到 input
  useEffect(() => {
    setInputUrl(baseUrl)
    setInputApiKey(apiKey)
  }, [baseUrl, apiKey])

  const handleSave = async () => {
    const trimmed = inputUrl.trim()
    if (!trimmed) {
      toast.error('请输入 MinerU 服务地址')
      return
    }
    // 补全 http:// 如果没写
    let finalUrl = trimmed
    if (!finalUrl.startsWith('http://') && !finalUrl.startsWith('https://')) {
      finalUrl = 'http://' + finalUrl
    }
    try {
      new URL(finalUrl)
    } catch {
      toast.error('请输入有效的 URL')
      return
    }
    setSaving(true)
    try {
      setBaseUrl(finalUrl)
      setApiKey(inputApiKey.trim())
      toast.success('MinerU 配置已保存')
    } finally {
      setSaving(false)
    }
  }

  const handleHealthCheck = useCallback(async () => {
    setChecking(true)
    setHealthStatus('unknown')
    setHealthMessage('')
    try {
      // 用当前输入框的值先测，不受保存影响
      const urlToCheck = inputUrl.trim().startsWith('http')
        ? inputUrl.trim()
        : 'http://' + inputUrl.trim()
      await checkMineruHealth(urlToCheck, inputApiKey.trim() || undefined)
      setHealthStatus('ok')
      setHealthMessage('MinerU 服务连接正常')
    } catch (e: any) {
      setHealthStatus('error')
      setHealthMessage(e?.message || '连接失败，请检查地址是否正确')
    } finally {
      setChecking(false)
    }
  }, [inputUrl, inputApiKey])

  // 保存后自动测一次
  useEffect(() => {
    if (saving === false && healthStatus === 'unknown') return
    // just loaded
  }, [saving])

  return (
    <div className="space-y-6 p-6">
      <div>
        <h2 className="text-2xl font-semibold">MinerU 文档解析配置</h2>
        <p className="mt-1 text-sm text-neutral-500">
          配置 MinerU PDF 解析服务的地址，保存后对所有文档上传任务生效
        </p>
      </div>

      {/* 地址配置 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <FileText className="h-5 w-5" />
            服务地址
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">MinerU Base URL</label>
            <Input
              placeholder="https://mineru.net/api/v4/extract/（云端）或 http://localhost:8080（本地）"
              value={inputUrl}
              onChange={e => {
                setInputUrl(e.target.value)
                setHealthStatus('unknown')
              }}
              className="max-w-md"
            />
            <p className="text-xs text-neutral-400">
              云端 API：https://mineru.net/api/v4/extract/ ｜ 本地部署：http://localhost:8080
            </p>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">API Key <span className="font-normal text-neutral-400">(可选)</span></label>
            <div className="relative max-w-md">
              <Input
                placeholder="sk-xxxxxxxxxxxxxxxx"
                type={showApiKey ? 'text' : 'password'}
                value={inputApiKey}
                onChange={e => setInputApiKey(e.target.value)}
                className="pr-10"
              />
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
                onClick={() => setShowApiKey(v => !v)}
              >
                {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
            <p className="text-xs text-neutral-400">
              如使用云端 MinerU API 服务，请输入 API Key。本地部署可留空。
            </p>
          </div>

          <div className="flex items-center gap-3">
            <Button onClick={handleSave} disabled={saving}>
              {saving ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-2 h-4 w-4" />
              )}
              保存配置
            </Button>

            <Button variant="outline" onClick={handleHealthCheck} disabled={checking}>
              {checking ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Activity className="mr-2 h-4 w-4" />
              )}
              健康检查
            </Button>
          </div>

          {healthStatus === 'ok' && (
            <Alert className="border-green-200 bg-green-50 text-green-800">
              <CheckCircle2 className="h-4 w-4" />
              <AlertDescription>{healthMessage}</AlertDescription>
            </Alert>
          )}
          {healthStatus === 'error' && (
            <Alert variant="destructive">
              <XCircle className="h-4 w-4" />
              <AlertDescription>{healthMessage}</AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* 使用说明 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <FileText className="h-5 w-5" />
            使用说明
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-neutral-600">
          <div className="flex items-start gap-2">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-medium text-blue-600">
              1
            </span>
            <p>
              <strong>MinerU</strong> 是一个 PDF 文档解析服务，需要单独部署并运行在指定地址。
              请参考 MinerU 官方文档进行部署。
            </p>
          </div>
          <div className="flex items-start gap-2">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-medium text-blue-600">
              2
            </span>
            <p>
              部署后，在上方输入 MinerU 服务的地址（默认端口 <code className="rounded bg-neutral-100 px-1">8080</code>），
              点击<strong>保存地址</strong>生效。
            </p>
          </div>
          <div className="flex items-start gap-2">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-medium text-blue-600">
              3
            </span>
            <p>
              点击<strong>健康检查</strong>可以验证服务连通性，确保地址配置正确。
            </p>
          </div>
          <div className="flex items-start gap-2">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-medium text-blue-600">
              4
            </span>
            <p>
              配置正确后，在<strong>首页</strong>选择「本地文档」平台，上传 PDF 文件即可自动解析。
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
