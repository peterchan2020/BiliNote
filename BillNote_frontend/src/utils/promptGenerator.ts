/**
 * Prompt 生成工具函数
 * 用于根据思维导图节点信息生成结构化的提示词
 */

/**
 * 节点点击信息接口
 */
/**
 * 增强的单个节点信息
 */
export interface NodeDetail {
  name: string           // 节点名称
  typeTag: string        // 类型标签
  treeIndex: string      // 节点树索引
  depth: number          // 节点深度
}

/**
 * 完整路径信息
 */
export interface PathInfo {
  treeIndex: string      // 完整索引路径
  depth: number          // 深度
  position: number       // 在兄弟节点中的位置（从1开始）
  totalSiblings: number  // 兄弟节点总数
}

export interface NodeClickInfo {
  content: string       // 节点文本（已清理HTML）
  typeTag: string       // 类型标签：概念/原理/方法/工具/案例/步骤/对比/公式/结论
  ancestors: string[]   // 从根到当前的祖先链
  siblings: string[]    // 同级兄弟节点
  depth: number         // 节点深度
  isLeaf: boolean       // 是否叶子节点
  treeIndex?: string    // 完整树索引，如 "1.2.3"
  // 新增字段
  ancestorsDetails?: NodeDetail[]  // 祖先节点完整信息
  parentDetail?: NodeDetail | null // 父节点完整信息
  siblingsDetails?: NodeDetail[]   // 兄弟节点完整信息
  childrenDetails?: NodeDetail[]   // 子节点信息
  pathInfo?: PathInfo              // 完整路径信息
}

/**
 * Prompt 生成结果接口
 */
export interface PromptResult {
  knowledge_point: string
  type: string
  context: {
    parent_chain: string[]
    siblings: string[]
    depth: number
  }
  prompt: string
  prompt_summary: string
}

/**
 * 类型标签到提示词模板的映射
 */
const PROMPT_TEMPLATES: Record<string, { focus: string; requirements: string[] }> = {
  '概念': {
    focus: '核心定义与概念辨析',
    requirements: [
      '给出该概念的核心定义，用简洁准确的语言描述其本质',
      '说明理解该概念需要的前置知识',
      '与相关概念进行区分，指出容易混淆的地方',
      '提供一个直观的类比或例子帮助理解'
    ]
  },
  '定义': {
    focus: '核心定义与概念辨析',
    requirements: [
      '给出该定义的严格表述和通俗解释',
      '说明理解该定义需要的前置知识',
      '与相关定义进行区分，指出容易混淆的地方',
      '提供一个直观的类比或例子帮助理解'
    ]
  },
  '原理': {
    focus: '底层逻辑与推导理解',
    requirements: [
      '阐述该原理的底层逻辑和核心思想',
      '给出推导过程或论证思路（如适用）',
      '提供直觉性的理解方式，为什么这个原理成立',
      '说明该原理的适用条件和边界'
    ]
  },
  '方法': {
    focus: '操作流程与实践指南',
    requirements: [
      '详细描述该方法的操作步骤和流程',
      '指出每个步骤的注意事项和关键点',
      '列举常见错误和避坑指南',
      '给出一个完整的应用示例'
    ]
  },
  '工具': {
    focus: '工具使用与最佳实践',
    requirements: [
      '介绍该工具的核心功能和使用场景',
      '说明基本的使用方法和操作流程',
      '分享使用技巧和最佳实践',
      '提供常见问题的解决方案'
    ]
  },
  '步骤': {
    focus: '分步指南与执行要点',
    requirements: [
      '按顺序详细描述每个步骤的具体操作',
      '说明步骤之间的逻辑关系和依赖',
      '指出每个步骤的关键点和容易出错的地方',
      '给出完整的执行示例'
    ]
  },
  '案例': {
    focus: '场景分析与解题思路',
    requirements: [
      '描述该案例的具体场景和背景',
      '详细分析解题思路和方法选择的原因',
      '给出完整的解答过程',
      '总结可以举一反三的规律和技巧'
    ]
  },
  '对比': {
    focus: '异同分析与选择建议',
    requirements: [
      '列出对比对象的相同点',
      '详细分析它们的不同点',
      '说明各自的适用场景',
      '给出选择建议和决策依据'
    ]
  },
  '公式': {
    focus: '公式解读与应用指南',
    requirements: [
      '解释公式的物理/数学含义',
      '详细说明每个变量的含义和取值范围',
      '指出公式的应用条件和局限性',
      '给出具体的计算示例'
    ]
  },
  '结论': {
    focus: '推导依据与适用范围',
    requirements: [
      '说明得出该结论的推导依据和论证过程',
      '明确该结论的适用范围和条件',
      '指出该结论的局限性和例外情况',
      '说明该结论的实际意义和应用价值'
    ]
  },
  '通用': {
    focus: '全面深入的知识讲解',
    requirements: [
      '给出该知识点的核心内容和要点',
      '解释其重要性和应用场景',
      '与相关知识建立联系',
      '提供易于理解的示例'
    ]
  }
}

/**
 * 清理 HTML 标签
 * @param html 包含 HTML 的字符串
 * @returns 清理后的纯文本
 */
function cleanHtml(html: string): string {
  if (!html) return ''
  // 移除 HTML 标签
  let text = html.replace(/<[^>]*>/g, '')
  // 解码 HTML 实体
  text = text
    .replace(/&nbsp;/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
  // 清理多余空白
  text = text.replace(/\s+/g, ' ').trim()
  return text
}

/**
 * 从节点内容中解析名称和类型标签
 * @param rawContent 原始节点内容（可能包含 HTML）
 * @returns 解析后的名称和类型标签
 */
export function parseNodeContent(rawContent: string): { name: string; typeTag: string } {
  // 先清理 HTML
  const cleanedContent = cleanHtml(rawContent)
  
  if (!cleanedContent) {
    return { name: '', typeTag: '通用' }
  }
  
  // 匹配最后一个方括号中的类型标签
  // 例如: "极限的定义 [定义]" -> name: "极限的定义", typeTag: "定义"
  // 例如: "函数 [概念] 的应用 [案例]" -> name: "函数 [概念] 的应用", typeTag: "案例"
  const tagMatch = cleanedContent.match(/^(.+?)\s*\[([^\]]+)\]\s*$/)
  
  if (tagMatch) {
    const name = tagMatch[1].trim()
    const typeTag = tagMatch[2].trim()
    return { name, typeTag }
  }
  
  // 尝试匹配内容中任意位置的方括号（取最后一个）
  const allTags = cleanedContent.match(/\[([^\]]+)\]/g)
  if (allTags && allTags.length > 0) {
    const lastTag = allTags[allTags.length - 1]
    const typeTag = lastTag.slice(1, -1).trim()
    // 移除最后一个标签，得到名称
    const lastIndex = cleanedContent.lastIndexOf(lastTag)
    const name = (cleanedContent.slice(0, lastIndex) + cleanedContent.slice(lastIndex + lastTag.length)).trim()
    return { name: name || cleanedContent, typeTag }
  }
  
  // 没有找到类型标签
  return { name: cleanedContent, typeTag: '通用' }
}

/**
 * 构建层级关系描述（增强版，优先使用详细信息）
 * @param ancestors 祖先节点链（名称数组）
 * @param ancestorsDetails 祖先节点详细信息数组
 * @returns 层级关系的文字描述
 */
function buildHierarchyDescription(ancestors: string[], ancestorsDetails?: NodeDetail[]): string {
  // 优先使用详细信息构建更丰富的描述
  if (ancestorsDetails && ancestorsDetails.length > 0) {
    const detailedParts = ancestorsDetails.map((ancestor) => {
      const typeInfo = ancestor.typeTag !== '通用' ? `[${ancestor.typeTag}]` : ''
      return `「${ancestor.name}」${typeInfo}`
    })
    const hierarchy = detailedParts.join(' > ')
    if (detailedParts.length === 1) {
      return `该知识点属于 ${detailedParts[0]} 的内容。`
    }
    return `该知识点的知识层级为：${hierarchy}。`
  }

  // 回退到旧的实现
  if (!ancestors || ancestors.length === 0) {
    return ''
  }

  const cleanedAncestors = ancestors.map(a => parseNodeContent(a).name).filter(Boolean)

  if (cleanedAncestors.length === 0) {
    return ''
  }

  if (cleanedAncestors.length === 1) {
    return `该知识点属于「${cleanedAncestors[0]}」的内容。`
  }

  return `该知识点的知识层级为：${cleanedAncestors.join(' > ')}。`
}

/**
 * 构建兄弟节点描述
 * @param siblings 兄弟节点列表
 * @param currentName 当前节点名称
 * @returns 兄弟节点的文字描述
 */
function buildSiblingsDescription(siblings: string[], currentName: string): string {
  if (!siblings || siblings.length === 0) {
    return ''
  }

  const cleanedSiblings = siblings
    .map(s => parseNodeContent(s).name)
    .filter(name => name && name !== currentName)

  if (cleanedSiblings.length === 0) {
    return ''
  }

  return `该知识点与「${cleanedSiblings.join('」、「')}」属于同一范畴，可以对比学习。`
}

/**
 * 构建父节点描述（使用增强信息）
 * @param parentDetail 父节点详细信息
 * @returns 父节点的文字描述
 */
function buildParentDescription(parentDetail: NodeDetail | null | undefined): string {
  if (!parentDetail) {
    return ''
  }
  return `其父节点为「${parentDetail.name}」[${parentDetail.typeTag}]。`
}

/**
 * 构建子节点概览（使用增强信息）
 * @param childrenDetails 子节点详细信息列表
 * @returns 子节点概览的文字描述
 */
function buildChildrenOverview(childrenDetails: NodeDetail[] | undefined): string {
  if (!childrenDetails || childrenDetails.length === 0) {
    return ''
  }

  const childNames = childrenDetails.map(child => `${child.name}[${child.typeTag}]`)
  if (childNames.length <= 3) {
    return `该主题下包含 ${childNames.length} 个子知识点：${childNames.join('、')}。`
  }

  return `该主题下包含 ${childNames.length} 个子知识点，主要包括：${childNames.slice(0, 3).join('、')}等。`
}

/**
 * 构建路径位置详情（使用增强信息）
 * @param pathInfo 路径信息
 * @returns 路径位置的文字描述
 */
function buildPathPositionDescription(pathInfo: PathInfo | undefined): string {
  if (!pathInfo) {
    return ''
  }

  const { position, totalSiblings } = pathInfo
  if (totalSiblings <= 1) {
    return ''
  }

  const positionDesc = position === 1 ? '第一个' : position === totalSiblings ? '最后一个' : `第 ${position} 个`
  return `在同级节点中，它是 ${positionDesc}（共 ${totalSiblings} 个同级节点）。`
}

/**
 * 根据节点信息生成结构化的 Prompt
 * @param info 节点点击信息
 * @returns Prompt 生成结果
 */
export function generateNodePrompt(info: NodeClickInfo): PromptResult {
  const { name: knowledgePoint } = parseNodeContent(info.content)
  const typeTag = info.typeTag || '通用'

  // 获取对应的模板，如果没有匹配则使用通用模板
  const template = PROMPT_TEMPLATES[typeTag] || PROMPT_TEMPLATES['通用']

  // 构建上下文描述（使用增强的祖先详细信息）
  const hierarchyDesc = buildHierarchyDescription(info.ancestors, info.ancestorsDetails)
  const siblingsDesc = buildSiblingsDescription(info.siblings, knowledgePoint)
  const depthDesc = info.depth > 0 ? `当前知识点位于第 ${info.depth} 层级。` : ''
  const leafDesc = info.isLeaf ? '这是一个具体的知识点，请深入讲解。' : '这是一个包含子知识点的主题，请先进行概述。'

  // 构建树索引描述
  const treeIndexDesc = info.treeIndex ? `该知识点在知识树中的位置索引为：${info.treeIndex}。` : ''

  // 使用增强信息构建额外描述
  const parentDesc = buildParentDescription(info.parentDetail)
  const childrenOverview = buildChildrenOverview(info.childrenDetails)
  const pathPositionDesc = buildPathPositionDescription(info.pathInfo)

  // 构建上下文部分
  const contextParts = [
    hierarchyDesc,
    siblingsDesc,
    depthDesc,
    treeIndexDesc,
    parentDesc,
    childrenOverview,
    pathPositionDesc,
    leafDesc
  ].filter(Boolean)
  const contextSection = contextParts.length > 0
    ? `\n\n## 上下文信息\n${contextParts.join('\n')}`
    : ''

  // 构建要求部分
  const requirementsSection = template.requirements
    .map((req, index) => `${index + 1}. ${req}`)
    .join('\n')

  // 生成完整的 Prompt
  const prompt = `你是一位专业的知识讲解专家，擅长将复杂的知识点讲解得深入浅出、通俗易懂。

## 任务
请针对知识点「${knowledgePoint}」进行详细讲解。
${contextSection}

## 讲解侧重
本次讲解侧重于：${template.focus}

## 具体要求
${requirementsSection}

## 输出格式要求
- 使用清晰的结构化格式，包含小标题和分点说明
- 语言要深入浅出，既保证准确性又要易于理解
- 适当使用类比、示例帮助理解
- 如有公式或专业术语，需要给出解释
- 总字数无限制，请尽可能全面地讲解该知识点。`

  // 生成摘要
  const prompt_summary = `请以「${template.focus}」为侧重点，详细讲解「${knowledgePoint}」这一${typeTag === '通用' ? '知识点' : typeTag}。`

  return {
    knowledge_point: knowledgePoint,
    type: typeTag,
    context: {
      parent_chain: info.ancestors,
      siblings: info.siblings,
      depth: info.depth
    },
    prompt,
    prompt_summary
  }
}

/**
 * 快速生成简短的提问 Prompt
 * @param content 节点内容
 * @param typeTag 类型标签
 * @returns 简短的提问 Prompt
 */
export function generateQuickPrompt(content: string, typeTag?: string): string {
  const { name, typeTag: parsedTag } = parseNodeContent(content)
  const finalTag = typeTag || parsedTag || '通用'
  const template = PROMPT_TEMPLATES[finalTag] || PROMPT_TEMPLATES['通用']
  
  return `请简要讲解「${name}」，侧重于${template.focus}。`
}
