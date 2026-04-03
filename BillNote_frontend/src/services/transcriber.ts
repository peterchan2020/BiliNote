import request from '@/utils/request'

export interface TranscriberConfig {
  transcriber_type: string
  whisper_model_size: string
  doubao_app_key: string
  doubao_api_key: string
  aliyun_api_key: string
  available_types: { value: string; label: string }[]
  whisper_model_sizes: string[]
  mlx_whisper_available: boolean
}

export interface ModelStatus {
  model_size: string
  downloaded: boolean
  downloading: boolean
}

export interface ModelsStatusResponse {
  whisper: ModelStatus[]
  mlx_whisper: ModelStatus[]
  mlx_available: boolean
}

export const getTranscriberConfig = async (): Promise<TranscriberConfig> => {
  return await request.get('/transcriber_config')
}

export const updateTranscriberConfig = async (data: {
  transcriber_type: string
  whisper_model_size?: string
  doubao_app_key?: string
  doubao_api_key?: string
  aliyun_api_key?: string
}) => {
  return await request.post('/transcriber_config', data)
}

export const getModelsStatus = async (): Promise<ModelsStatusResponse> => {
  return await request.get('/transcriber_models_status')
}

export const downloadModel = async (data: {
  model_size: string
  transcriber_type?: string
}) => {
  return await request.post('/transcriber_download', data)
}

export const testDoubaoConnection = async (data: {
  doubao_app_key: string
  doubao_api_key: string
}) => {
  return await request.post('/doubao_test', data)
}

export const testAliyunConnection = async (data: {
  aliyun_api_key: string
}) => {
  return await request.post('/aliyun_test', data)
}
