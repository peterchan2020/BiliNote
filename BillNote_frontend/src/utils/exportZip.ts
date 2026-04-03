import JSZip from "jszip"

/**
 * 从 markdown 文本中提取所有本地图片路径
 * 匹配格式: ![](/static/xxx.jpg) 或 ![alt](/static/xxx.jpg)
 * 排除远程 URL（http/https 开头）
 */
function extractImagePaths(markdown: string): string[] {
  const regex = /!\[([^\]]*)\]\(([^)]+)\)/g
  const paths: string[] = []
  let match

  while ((match = regex.exec(markdown)) !== null) {
    const url = match[2]
    if (url.startsWith("/static/") && !url.startsWith("http")) {
      paths.push(url)
    }
  }

  return [...new Set(paths)]
}

/**
 * 将 markdown 中的 /static/xxx 路径替换为相对路径 ./images/filename
 */
function rewriteImagePaths(markdown: string): string {
  return markdown.replace(/!\[([^\]]*)\]\(\/static\/[^/]+\/([^)]+)\)/g, (_, alt, filename) => {
    return `![${alt}](./images/${filename})`
  })
}

/**
 * 从后端 fetch 图片文件并返回 blob
 */
async function fetchImage(path: string, baseURL: string): Promise<Blob> {
  const url = `${baseURL.replace(/\/$/, "")}${path}`
  const resp = await fetch(url)
  if (!resp.ok) {
    throw new Error(`Failed to fetch image: ${url} (${resp.status})`)
  }
  return resp.blob()
}

/**
 * 导出 markdown + 图片为 zip 文件
 * @param markdown 原始 markdown 内容
 * @param fileName 笔记标题（不含扩展名）
 * @param baseURL 后端基础 URL（如 http://localhost:8483）
 * @param onProgress 进度回调 (current, total)
 */
export async function exportNoteAsZip(
  markdown: string,
  fileName: string,
  baseURL: string,
  onProgress?: (current: number, total: number) => void,
): Promise<void> {
  const zip = new JSZip()

  const imagePaths = extractImagePaths(markdown)
  const total = imagePaths.length

  // 重写 markdown 图片路径为相对路径
  const rewrittenMarkdown = rewriteImagePaths(markdown)
  zip.file(`${fileName}.md`, rewrittenMarkdown)

  // 创建 images 文件夹
  const imagesFolder = zip.folder("images")!

  // 逐个下载图片并添加到 zip
  for (let i = 0; i < imagePaths.length; i++) {
    const path = imagePaths[i]
    const blob = await fetchImage(path, baseURL)
    const filename = path.split("/").pop()!
    imagesFolder.file(filename, blob)
    onProgress?.(i + 1, total)
  }

  // 生成 zip 文件并触发下载
  const zipBlob = await zip.generateAsync({ type: "blob" })
  const url = URL.createObjectURL(zipBlob)
  const link = document.createElement("a")
  link.href = url
  link.download = `${fileName}.zip`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}
