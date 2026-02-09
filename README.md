# SpringBoot 调用链分析器（支持 LLM 辅助）

这是一个面向 SpringBoot 工程的轻量级静态调用链分析器原型。

## 目标

给定：
- 一个工程目录
- 一个 Java 接口（可选指定方法）

输出：
- 该接口在工程内的主要调用链路
- 树状结构展示
- 对静态分析难以判定的节点，自动调用 LLM 辅助推断

## 快速开始

```bash
python3 springboot_callchain_analyzer.py \
  --project-dir /path/to/springboot-project \
  --interface com.demo.api.UserService \
  --method getUserById
```

如果不传 `--method`，则会分析该接口定义的所有方法。

## LLM 辅助（可选）

默认不启用 LLM。启用方式：

```bash
export OPENAI_API_KEY="your_api_key"
export OPENAI_BASE_URL="https://api.openai.com/v1"  # 可选
export OPENAI_MODEL="gpt-4o-mini"                    # 可选

python3 springboot_callchain_analyzer.py \
  --project-dir /path/to/project \
  --interface com.demo.api.UserService \
  --method getUserById \
  --use-llm
```

LLM 仅在以下场景触发：
- 调用方中存在无法静态解析的方法调用。
- 调用目标涉及动态代理、反射、复杂泛型、框架注入等导致静态规则不足。

## 输出示例

```text
com.demo.api.UserService#getUserById
└── com.demo.service.impl.UserServiceImpl#getUserById
    ├── com.demo.repository.UserRepository#findById
    ├── com.demo.mapper.UserMapper#toDto
    └── [LLM] 可能调用: com.demo.cache.UserCache#get
```

## 已实现能力

- Java 文件扫描与基础结构解析（package/class/interface/method）。
- 接口到实现类的方法入口定位。
- 方法体内调用提取（对象调用、直接调用）。
- 简单 Spring 依赖字段类型推断（按字段类型连接到候选类）。
- 递归构建调用树（深度可控）。
- LLM 辅助猜测无法静态解析的候选调用。

## 局限

当前版本为原型，解析基于正则和语法近似，不等价于完整 Java AST：
- 对重载、泛型、继承层级的精确分派能力有限。
- 对 Lambda、匿名类、AOP 代理、运行时 Bean 装配场景仅部分覆盖。

建议下一步结合：
- JavaParser / Eclipse JDT 做高保真 AST。
- 字节码分析（ASM/Soot）补足运行时分派。
- Spring Bean 图提取（读取注解、配置类、条件装配）。
