/**
 * promptGenerator 工具函数测试
 * TDD: RED阶段 - 先编写失败的测试
 */

import { parseNodeContent, generateNodePrompt, generateQuickPrompt } from '@/utils/promptGenerator';

// 常量
const KNOWLEDGE_GRAPH_MAX_DEPTH = 6;
const ATOMIC_TYPE_TAGS = ['结论', '公式'];
const DECOMPOSABLE_TYPES = ['概念', '原理', '方法', '工具', '案例', '步骤', '对比'];

describe('parseNodeContent', () => {
  describe('基本解析功能', () => {
    it('应该解析带类型标签的节点', () => {
      const result = parseNodeContent('导数的定义 [定义]');
      expect(result.name).toBe('导数的定义');
      expect(result.typeTag).toBe('定义');
    });

    it('应该解析概念类型', () => {
      const result = parseNodeContent('极限 [概念]');
      expect(result.name).toBe('极限');
      expect(result.typeTag).toBe('概念');
    });

    it('应该解析公式类型', () => {
      const result = parseNodeContent('欧拉公式 [公式]');
      expect(result.name).toBe('欧拉公式');
      expect(result.typeTag).toBe('公式');
    });

    it('应该解析结论类型', () => {
      const result = parseNodeContent('重要结论 [结论]');
      expect(result.name).toBe('重要结论');
      expect(result.typeTag).toBe('结论');
    });

    it('应该处理没有类型标签的节点', () => {
      const result = parseNodeContent('一些文本');
      expect(result.name).toBe('一些文本');
      expect(result.typeTag).toBe('通用');
    });

    it('应该处理空字符串', () => {
      const result = parseNodeContent('');
      expect(result.name).toBe('');
      expect(result.typeTag).toBe('通用');
    });

    it('应该处理null输入', () => {
      const result = parseNodeContent(null as any);
      expect(result.name).toBe('');
      expect(result.typeTag).toBe('通用');
    });

    it('应该处理undefined输入', () => {
      const result = parseNodeContent(undefined as any);
      expect(result.name).toBe('');
      expect(result.typeTag).toBe('通用');
    });
  });

  describe('HTML清理', () => {
    it('应该移除HTML标签', () => {
      const result = parseNodeContent('<span>测试</span> [概念]');
      expect(result.name).toBe('测试');
      expect(result.typeTag).toBe('概念');
    });

    it('应该处理混合内容', () => {
      const result = parseNodeContent('<div>节点<span>内容</span></div> [方法]');
      expect(result.name).toBe('节点内容');
      expect(result.typeTag).toBe('方法');
    });
  });

  describe('特殊字符处理', () => {
    it('应该处理Unicode字符', () => {
      const result = parseNodeContent('ε-δ语言 [方法]');
      expect(result.name).toBe('ε-δ语言');
      expect(result.typeTag).toBe('方法');
    });

    it('应该处理中文节点名', () => {
      const result = parseNodeContent('微积分入门 [主题]');
      expect(result.name).toBe('微积分入门');
      expect(result.typeTag).toBe('主题');
    });

    it('应该处理带空格的节点名', () => {
      const result = parseNodeContent('导数的几何意义 [原理]');
      expect(result.name).toBe('导数的几何意义');
      expect(result.typeTag).toBe('原理');
    });
  });

  describe('深度验证', () => {
    it('应该识别原子类型', () => {
      ATOMIC_TYPE_TAGS.forEach(tag => {
        const result = parseNodeContent(`节点 [${tag}]`);
        expect(result.typeTag).toBe(tag);
      });
    });

    it('应该识别可分解类型', () => {
      DECOMPOSABLE_TYPES.forEach(tag => {
        const result = parseNodeContent(`节点 [${tag}]`);
        expect(result.typeTag).toBe(tag);
      });
    });

    it('最大深度常量应为6', () => {
      expect(KNOWLEDGE_GRAPH_MAX_DEPTH).toBe(6);
    });
  });
});

describe('generateNodePrompt', () => {
  describe('基本功能', () => {
    it('应该生成包含知识点的prompt', () => {
      const nodeInfo = {
        content: '导数 [概念]',
        typeTag: '概念',
        ancestors: [],
        siblings: [],
        depth: 1,
        isLeaf: true,
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.knowledge_point).toBe('导数');
      expect(result.type).toBe('概念');
      expect(result.prompt).toContain('导数');
    });

    it('应该包含上下文信息', () => {
      const nodeInfo = {
        content: '极限 [概念]',
        typeTag: '概念',
        ancestors: ['微积分'],
        siblings: ['连续性'],
        depth: 2,
        isLeaf: false,
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.context.parent_chain).toEqual(['微积分']);
      expect(result.context.siblings).toEqual(['连续性']);
      expect(result.context.depth).toBe(2);
    });
  });

  describe('节点类型处理', () => {
    it('应该处理概念类型', () => {
      const nodeInfo = {
        content: '概念节点 [概念]',
        typeTag: '概念',
        ancestors: [],
        siblings: [],
        depth: 0,
        isLeaf: true,
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.type).toBe('概念');
      expect(result.prompt).toContain('核心定义与概念辨析');
    });

    it('应该处理方法类型', () => {
      const nodeInfo = {
        content: '方法节点 [方法]',
        typeTag: '方法',
        ancestors: [],
        siblings: [],
        depth: 0,
        isLeaf: true,
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.type).toBe('方法');
      expect(result.prompt).toContain('操作流程与实践指南');
    });

    it('应该处理公式类型', () => {
      const nodeInfo = {
        content: '公式节点 [公式]',
        typeTag: '公式',
        ancestors: [],
        siblings: [],
        depth: 0,
        isLeaf: true,
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.type).toBe('公式');
      expect(result.prompt).toContain('公式解读与应用指南');
    });

    it('应该处理结论类型', () => {
      const nodeInfo = {
        content: '结论节点 [结论]',
        typeTag: '结论',
        ancestors: [],
        siblings: [],
        depth: 0,
        isLeaf: true,
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.type).toBe('结论');
      expect(result.prompt).toContain('推导依据与适用范围');
    });

    it('应该处理通用类型', () => {
      const nodeInfo = {
        content: '通用节点',
        typeTag: '通用',
        ancestors: [],
        siblings: [],
        depth: 0,
        isLeaf: true,
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.type).toBe('通用');
      expect(result.prompt).toContain('全面深入的知识讲解');
    });
  });

  describe('增强信息', () => {
    it('应该处理ancestorsDetails', () => {
      const nodeInfo = {
        content: '子节点 [概念]',
        typeTag: '概念',
        ancestors: ['父节点'],
        siblings: [],
        depth: 2,
        isLeaf: true,
        ancestorsDetails: [
          { name: '父节点', typeTag: '概念', treeIndex: '1', depth: 1 }
        ],
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.prompt).toContain('父节点');
    });

    it('应该处理siblingsDetails', () => {
      const nodeInfo = {
        content: '当前节点 [方法]',
        typeTag: '方法',
        ancestors: [],
        siblings: ['兄弟节点'],
        depth: 1,
        isLeaf: false,
        siblingsDetails: [
          { name: '兄弟节点', typeTag: '方法', treeIndex: '1.2', depth: 1 }
        ],
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.prompt).toContain('兄弟节点');
    });

    it('应该处理childrenDetails', () => {
      const nodeInfo = {
        content: '父节点 [概念]',
        typeTag: '概念',
        ancestors: [],
        siblings: [],
        depth: 1,
        isLeaf: false,
        childrenDetails: [
          { name: '子节点1', typeTag: '概念', treeIndex: '2.1', depth: 2 },
          { name: '子节点2', typeTag: '方法', treeIndex: '2.2', depth: 2 }
        ],
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.prompt).toContain('子节点1');
      expect(result.prompt).toContain('子节点2');
    });

    it('应该处理pathInfo', () => {
      const nodeInfo = {
        content: '节点 [概念]',
        typeTag: '概念',
        ancestors: [],
        siblings: ['兄弟1', '兄弟2'],
        depth: 1,
        isLeaf: false,
        pathInfo: {
          treeIndex: '2.1',
          depth: 1,
          position: 1,
          totalSiblings: 3
        },
      };
      const result = generateNodePrompt(nodeInfo);
      // pathInfo包含position和totalSiblings，用于描述同级节点位置
      expect(result.prompt).toContain('第一个');
      expect(result.prompt).toContain('3 个同级节点');
    });
  });

  describe('边界情况', () => {
    it('应该处理空ancestors', () => {
      const nodeInfo = {
        content: '根节点 [主题]',
        typeTag: '主题',
        ancestors: [],
        siblings: [],
        depth: 0,
        isLeaf: false,
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.context.parent_chain).toEqual([]);
    });

    it('应该处理空siblings', () => {
      const nodeInfo = {
        content: '唯一节点 [概念]',
        typeTag: '概念',
        ancestors: [],
        siblings: [],
        depth: 0,
        isLeaf: true,
      };
      const result = generateNodePrompt(nodeInfo);
      expect(result.context.siblings).toEqual([]);
    });

    it('应该处理叶节点标识', () => {
      const leafNode = {
        content: '叶节点 [结论]',
        typeTag: '结论',
        ancestors: [],
        siblings: [],
        depth: 3,
        isLeaf: true,
      };
      const result = generateNodePrompt(leafNode);
      expect(result.prompt).toContain('深入讲解');
    });

    it('应该处理非叶节点标识', () => {
      const nonLeafNode = {
        content: '分支节点 [概念]',
        typeTag: '概念',
        ancestors: [],
        siblings: [],
        depth: 2,
        isLeaf: false,
      };
      const result = generateNodePrompt(nonLeafNode);
      expect(result.prompt).toContain('概述');
    });
  });
});

describe('generateQuickPrompt', () => {
  it('应该生成简短prompt', () => {
    const result = generateQuickPrompt('导数 [概念]');
    expect(result).toContain('导数');
    expect(result).toContain('讲解');
  });

  it('应该使用解析出的类型标签', () => {
    const result = generateQuickPrompt('公式节点 [公式]');
    expect(result).toContain('公式解读与应用指南');
  });

  it('应该处理没有类型标签的内容', () => {
    const result = generateQuickPrompt('普通文本');
    expect(result).toContain('普通文本');
  });

  it('应该允许覆盖类型标签', () => {
    const result = generateQuickPrompt('节点名', '概念');
    expect(result).toContain('节点名');
    expect(result).toContain('核心定义与概念辨析');
  });
});
