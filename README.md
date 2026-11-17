# UnityExtractor

面向 Unity 游戏的证据驱动扫描与通用资源检查工具。项目采用模块化设计，将 Unity 容器解析与具体游戏的数据解释分离。

> 当前状态：**P0、P1 对象枚举、P1.1 引用关系分析已通过 Phigros 真实 APK 验证；P1.2 依赖图分析已实现，等待真实样本验收**。项目通过可选 UnityPy 后端枚举 SerializedFile / AssetBundle 对象，并可分析对象引用及依赖关系；当前尚不导出资源文件。

## 环境要求

- Python 3.10 或更高版本
- P0 扫描器不需要第三方运行时依赖
- P1 对象枚举使用可选依赖 UnityPy

## 安装

仅使用 P0 扫描器：

```bash
python -m pip install -e .
```

安装开发测试依赖：

```bash
python -m pip install -e ".[dev]"
```

安装 P1 Unity 解析后端：

```bash
python -m pip install -e ".[unity]"
```

同时安装测试和 Unity 解析依赖：

```bash
python -m pip install -e ".[dev,unity]"
```

## P0：扫描

```bash
unity-extractor /path/to/game.apk
unity-extractor /path/to/unpacked/game --format json
python -m unity_extractor /path/to/resource.bundle --output scan-report.json
```

支持的输入：

- APK 和 ZIP 压缩包；
- Unity 游戏目录；
- 独立 Unity Bundle 或 SerializedFile。

扫描结果包含 Unity 运行时和数据文件证据、UnityFS 等 Bundle 签名、SerializedFile 候选、Addressables 文件名候选、StreamingAssets 路径、Mono / IL2CPP 线索、版本字符串、置信度和诊断信息。

非 Unity 识别目前包含对 `libcocos2dcpp.so` 的保守指纹判断。这只是辅助识别，不代表支持解析或提取 Cocos2d-x 资源。如果 Unity 与 Cocos2d-x 强证据同时出现，引擎结果保持为 `unknown`。

缺少某项证据不代表对应功能一定不存在。加密、混淆、自定义封装或特殊目录布局可能导致扫描器无法识别。版本和脚本后端判断均为启发式，证据不足时会保留为 unknown。

## P1：对象枚举（已完成首轮验收）

安装 UnityPy 后：

```bash
unity-extractor /path/to/game.apk --enumerate-objects --format json --output objects.json
unity-extractor /path/to/game.bundle --enumerate-objects
unity-extractor /path/to/game.apk --enumerate-objects --analyze-references --format json --output references.json
```

也可以扫描解包后的游戏目录。默认单个候选容器最大为 512 MiB，可通过 `--max-container-mb` 调整。

P1 当前输出对象类型、名称（可读取时）、Path ID、字节大小、容器来源、容器内路径和部分 SerializedFile 元数据。对象名称可能重复，因此对象身份由来源、容器和 Path ID 共同确定。单个对象名称或容器路径读取失败时，会保留对象记录并附加 warning。

APK / ZIP 输入只会选择具有 Unity Bundle 或 SerializedFile 头部特征的成员，使用随机临时文件交给解析后端，并在处理后清理；不会按压缩包内路径直接写入文件系统。P1 仍有单容器、总容器字节数、容器数量和对象数量限制。

Phigros APK 的真实样本已成功枚举 7,804 个对象，处理 2,683 个容器，第二版报告为 0 warnings / 0 errors。该结果验证的是对象元数据枚举，不代表资源 payload 已解码或导出。

## P1.1：对象引用关系（首轮实现）

使用 `--analyze-references` 可额外解析对象的序列化 TypeTree，提取其中的 Unity `PPtr` 引用。输出报告新增顶层 `references` 数组，记录来源对象、目标对象（可解析时）、目标 Path ID、File ID 和解析状态。无法解析的指针仍保留原始 File ID / Path ID，不会伪造目标对象。Phigros 真实 APK 样本提取到 8,156 条引用，其中 8,112 条成功解析，44 条保留为未解析外部引用；来源和已解析目标均能在对象清单中找到。

引用分析会读取对象的结构化序列化数据，但不会执行游戏代码，也不会导出或转换纹理、音频、网格等资源。单对象遍历设有节点数和深度上限；某个对象解析失败时记录 warning 并继续处理其他对象。UnityPy 不支持的 TypeTree、缺失的外部文件或无法解析的 PPtr 都可能产生未解析引用。

P1.1 已通过 Phigros 真实 APK 验收：7,804 个对象、8,156 条引用，8,112 条成功解析，44 条未解析引用均保留为外部引用记录。

## P1.2：资源依赖图分析（首轮实现）

在 P1.1 引用数据上增加 `--analyze-dependencies`，要求同时启用 `--enumerate-objects` 和 `--analyze-references`：

```bash
unity-extractor /path/to/game.apk \
  --enumerate-objects \
  --analyze-references \
  --analyze-dependencies \
  --format json \
  --output dependency-report.json
```

输出新增顶层 `dependency_graph`：
- `nodes`：对象节点及直接依赖数、反向引用数；
- `edges`：从引用对象指向被引用对象，按对象对去重，并保留原始引用条数；
- `unreferenced_resource_ids`：没有其他对象引用的对象；
- `isolated_resource_ids`：既不引用其他对象，也没有对象引用它的对象；
- `terminal_resource_ids`：没有进一步依赖的对象；
- `cycles`：强连通分量形式表示的循环依赖；
- `summary`：节点、边、未解析引用、悬空引用、孤立对象和循环数量。

可以通过 `--dependency-root <resource_id>` 指定一个对象，输出从该对象出发的最短依赖链；默认最大深度为 8，可通过 `--dependency-depth` 调整。依赖链只记录每个可达对象的一条最短路径，避免枚举所有可能路径造成组合爆炸。

未解析引用和目标不在对象清单中的悬空引用不会被伪装成有效边，而是单独计数。依赖图仅代表当前扫描输入和已解析 PPtr 所能证明的关系，不等价于游戏运行时的完整加载依赖。

P1.2 已通过 Phigros 真实 APK 验收：7,804 个节点、8,112 条有效边、58 个循环依赖分量；节点度数、边端点和强连通分量均通过独立核验。

## 安全与限制

- 不执行游戏包内代码。
- 不将 APK / ZIP 内容整体解压到用户目录。
- 限制候选容器大小、总处理字节数、容器数量和对象数量。
- 临时文件使用系统随机路径，并在处理后清理。
- 解析失败和不支持的对象必须保留诊断信息。

## 开发与测试

```bash
python -m pip install -e ".[dev,unity]"
python -m pytest -v
```
