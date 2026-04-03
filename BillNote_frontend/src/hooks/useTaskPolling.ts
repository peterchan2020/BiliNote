import { useEffect, useRef } from 'react'
import { useTaskStore } from '@/store/taskStore'
import { get_task_status } from '@/services/note.ts'
import { getMineruTaskStatus } from '@/services/mineru'
import toast from 'react-hot-toast'

export const useTaskPolling = (interval = 3000) => {
  const tasks = useTaskStore(state => state.tasks)
  const updateTaskContent = useTaskStore(state => state.updateTaskContent)
  const removeTask = useTaskStore(state => state.removeTask)

  const tasksRef = useRef(tasks)

  // 每次 tasks 更新，把最新的 tasks 同步进去
  useEffect(() => {
    tasksRef.current = tasks
  }, [tasks])

  useEffect(() => {
    const timer = setInterval(async () => {
      const pendingTasks = tasksRef.current.filter(
        task => task.status != 'SUCCESS' && task.status != 'FAILED'
      )

      // 无活跃任务时跳过轮询
      if (pendingTasks.length === 0) return

      for (const task of pendingTasks) {
        try {
          if (task.platform === 'local_doc') {
            // Poll MinerU for document tasks
            const res = await getMineruTaskStatus(task.id)
            // 注意：后端 R.success 包装了一层 { code, msg, data }，
            // 所以 status 在 res.data.status，不是 res.status
            const mineruStatus = res.data?.status
            const mineruResult = res.data?.result
            console.log("[MinerU Poll]", task.id, "→", JSON.stringify(res));

            if (mineruStatus && mineruStatus !== task.status) {
              if (mineruStatus === 'SUCCESS') {
                toast.success('文档解析成功')
                updateTaskContent(task.id, {
                  status: 'SUCCESS',
                  markdown: mineruResult?.md_content || '',
                  transcript: { full_text: '', language: '', raw: null, segments: [] },
                  audioMeta: {
                    cover_url: '',
                    duration: 0,
                    file_path: '',
                    platform: 'local_doc',
                    raw_info: null,
                    title: task.formData.video_url || '文档笔记',
                    video_id: task.id,
                  },
                  knowledge_graph: undefined,
                })
              } else if (mineruStatus === 'FAILED') {
                updateTaskContent(task.id, { status: mineruStatus })
                // MinerU FAILED: message 嵌套在 res.data.data.message
                const failMsg = res.data?.data?.message || res.data?.msg || '文档解析失败'
                toast.error(`文档解析失败: ${failMsg}`)
              } else {
                updateTaskContent(task.id, { status: mineruStatus })
              }
            }
          } else {
            // Existing video task polling
            const res = await get_task_status(task.id)
            const { status } = res

            if (status && status !== task.status) {
              if (status === 'SUCCESS') {
                const { markdown, transcript, audio_meta, knowledge_graph } = res.result
                toast.success('笔记生成成功')
                updateTaskContent(task.id, {
                  status,
                  markdown,
                  transcript,
                  audioMeta: audio_meta,
                  knowledge_graph,
                })
              } else if (status === 'FAILED') {
                updateTaskContent(task.id, { status })
                toast.error('笔记生成失败')
              } else {
                updateTaskContent(task.id, { status })
              }
            }
          }
        } catch (e: any) {
          // 拦截器 reject 的是原始 body: {code, msg, data}，HTTP 错误才是 e.response.data
          const errorMsg = e?.data?.data?.message || e?.data?.message || e?.data?.msg || e?.response?.data?.msg || e?.message || '任务执行失败'
          updateTaskContent(task.id, { status: 'FAILED' })
          toast.error(`任务执行失败: ${errorMsg}`)
        }
      }
    }, interval)

    return () => clearInterval(timer)
  }, [interval])
}
