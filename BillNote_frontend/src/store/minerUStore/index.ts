import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface MinerUState {
  baseUrl: string
  apiKey: string
  setBaseUrl: (url: string) => void
  setApiKey: (key: string) => void
}

export const useMinerUStore = create<MinerUState>()(
  persist(
    set => ({
      baseUrl: 'https://mineru.net/api/v4/extract/',
      apiKey: '',
      setBaseUrl: url => set({ baseUrl: url }),
      setApiKey: key => set({ apiKey: key }),
    }),
    {
      name: 'mineru-store',
    }
  )
)
