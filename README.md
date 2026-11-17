# UnityExtractor

面向 Unity 游戏的证据驱动扫描与通用资源检查工具。项目采用模块化设计，将 Unity 容器解析与具体游戏的数据解释分离。

## 环境要求

- Python 3.10 或更高版本
- P0 扫描器不需要第三方运行时依赖
- 对象枚举和资源分析使用可选依赖 UnityPy

## 安装

仅使用基础扫描功能：

```bash
python -m pip install -e .
```

安装开发测试依赖：

```bash
python -m pip install -e ".[dev]"
```

安装 Unity 解析后端：

```bash
python -m pip install -e ".[unity]"
```

同时安装测试和 Unity 解析依赖：

```bash
python -m pip install -e ".[dev,unity]"
```

## 基础扫描

```bash
unity-extractor /path/to/game.apk
unity-extractor /path/to/unpacked/game --format json
python -m unity_extractor /path/to/resource.bundle --output scan-report.json
```

支持的输入：

- APK 和 ZIP 压缩包；
- Unity 游戏目录；
- 独立 Unity Bundle 或 SerializedFile。

扫描结果可包含 Unity 运行时和数据文件证据、UnityFS 等 Bundle 签名、SerializedFile 候选、Addressables 文件名候选、StreamingAssets 路径、Mono / IL2CPP 线索、版本字符串、置信度和诊断信息。

非 Unity 识别包含对 `libcocos2dcpp.so` 的保守指纹判断。这只是辅助识别，不代表支持解析或提取 Cocos2d-x 资源。如果 Unity 与 Cocos2d-x 强证据同时出现，引擎结果保持为 `unknown`。

缺少某项证据不代表对应功能一定不存在。加密、混淆、自定义封装或特殊目录布局可能导致扫描器无法识别。版本和脚本后端判断均为启发式，证据不足时会保留为 unknown。

## 对象枚举

安装 UnityPy 后：

```bash
unity-extractor /path/to/game.apk --enumerate-objects --format json --output objects.json
unity-extractor /path/to/game.bundle --enumerate-objects
```

也可以扫描解包后的游戏目录。默认单个候选容器最大为 512 MiB，可通过 `--max-container-mb` 调整。

对象信息包括对象类型、名称（可读取时）、Path ID、字节大小、容器来源、容器内路径和部分 SerializedFile 元数据。对象名称可能重复，因此对象身份由来源、容器和 Path ID 共同确定。单个对象名称或容器路径读取失败时，会保留对象记录并附加 warning。

APK / ZIP 输入只会选择具有 Unity Bundle 或 SerializedFile 头部特征的成员，使用随机临时文件交给解析后端，并在处理后清理；不会按压缩包内路径直接写入文件系统。扫描过程设有单容器、总容器字节数、容器数量和对象数量限制。

## 对象引用分析

使用 `--analyze-references` 可额外解析对象的序列化 TypeTree，提取其中的 Unity `PPtr` 引用：

```bash
unity-extractor /path/to/game.apk --enumerate-objects --analyze-references --format json --output references.json
```

引用记录包含来源对象、目标对象（可解析时）、目标 Path ID、File ID 和解析状态。无法解析的指针仍保留原始 File ID / Path ID，不会伪造目标对象。

引用分析会读取对象的结构化序列化数据，但不会执行游戏代码，也不会导出或转换纹理、音频、网格等资源。单对象遍历设有节点数和深度上限；某个对象解析失败时记录 warning 并继续处理其他对象。UnityPy 不支持的 TypeTree、缺失的外部文件或无法解析的 PPtr 都可能产生未解析引用。

## 资源依赖图分析

在引用分析基础上增加 `--analyze-dependencies`，需要同时启用对象枚举和引用分析：

```bash
unity-extractor /path/to/game.apk \
  --enumerate-objects \
  --analyze-references \
  --analyze-dependencies \
  --format json \
  --output dependency-report.json
```

输出的 `dependency_graph` 包含：

- `nodes`：对象节点及直接依赖数、反向引用数；
- `edges`：从引用对象指向被引用对象的关系；
- `unreferenced_resource_ids`：没有其他对象引用的对象；
- `isolated_resource_ids`：既不引用其他对象，也没有对象引用它的对象；
- `terminal_resource_ids`：没有进一步依赖的对象；
- `cycles`：以强连通分量形式表示的循环依赖；
- `summary`：节点、边、未解析引用、悬空引用、孤立对象和循环数量。

可以通过 `--dependency-root <resource_id>` 指定一个对象，输出从该对象出发的最短依赖链；默认最大深度为 8，可通过 `--dependency-depth` 调整。依赖链只记录每个可达对象的一条最短路径，避免枚举所有可能路径造成组合爆炸。

未解析引用和目标不在对象清单中的悬空引用不会被伪装成有效边，而是单独计数。依赖图仅代表当前扫描输入和已解析 PPtr 所能证明的关系，不等价于游戏运行时的完整加载依赖。

## 资源类型分类

对象清单提供保守的资源分类字段：`resource_category`、`resource_subcategory`、`classification_confidence` 和 `classification_basis`。分类依据 Unity 原生类型名称精确匹配；未知类型保留为 `unknown`。该功能只增加对象元数据，不解码或导出资源 Payload。

## 资源导出（P2）

当前资源导出功能处于逐步实现阶段。P2.1 首先支持 `TextAsset`，会在 Unity 容器仍处于加载状态时读取 Payload，避免 APK 临时暂存文件清理后无法再次访问对象。

```bash
unity-extractor /path/to/game.apk \
  --enumerate-objects \
  --export-resources \
  --export-dir ./unity-export \
  --format json \
  --output export-report.json
```

P2.1 的导出结果写入 `--export-dir` 指定目录：
- `text/`：TextAsset 内容，以原始字节写出；不假设内容一定是 UTF-8 文本。
- `export-manifest.json`：记录资源身份、来源容器、Path ID、导出状态、文件相对路径、字节数和 SHA-256。
- 文件名由清理后的资源名称、Path ID 和资源身份摘要组成；资源名称不会被当作目录路径使用。

单个 TextAsset 读取或写入失败会记录在清单中，并继续处理其他对象。P2.2 已加入 `Texture2D` 和 `Sprite` 图像导出，统一写为 PNG，分别放入 `images/texture2d/` 和 `images/sprite/`。清单记录解码后尺寸、图像模式、Unity 源纹理格式（可读取时）、文件大小和 SHA-256；超过 1 亿像素、空图像或解码失败的对象会单独标记失败并继续处理。

P2.3 已加入 `AudioClip` 样本导出，写入 `audio/`。样本保持后端返回的格式（`.wav` / `.ogg` / `.m4a`），未知格式保留为 `.bin` 原始字节，不进行未经验证的转码。多样本 AudioClip 会分别导出多个文件，并在清单中通过同一来源对象、`sample_index` 和 `sample_name` 关联。清单同时记录声道数、采样率、时长、位深、源压缩格式和加载类型（可读取时）。单个样本失败或为空时记录 warning 并继续导出其余样本；只要存在失败样本，该 AudioClip 在清单中标记为 failed，已成功导出的样本仍会保留。没有任何可导出样本的 AudioClip 同样标记为失败。对象枚举报告与导出清单是两个独立输出文件。

## 安全与限制

- 不执行游戏包内代码。
- 不将 APK / ZIP 内容整体解压到用户目录。
- 限制候选容器大小、总处理字节数、容器数量和对象数量。
- 临时文件使用系统随机路径，并在处理后清理。
- 解析失败和不支持的对象保留诊断信息。

## 开发测试

```bash
python -m pip install -e ".[dev,unity]"
python -m pytest -v
```


## 导出压缩包验证

可以在本地将导出目录压缩为 ZIP，再使用独立验证命令逐个核对 ZIP 内的文件与导出清单。验证器直接从 ZIP 流式读取数据，不会将整个压缩包解压到磁盘。

建议从导出目录内部创建 ZIP，使 ZIP 内部路径与 Manifest 的相对路径一致，并将清单一并放在 ZIP 根目录：

    cd ./unity-export
    zip -r -1 ../unity-export.zip *

安装项目后即可验证：

    unity-extractor-verify-archive ../unity-export.zip \
      --manifest ./export-manifest.json \
      --output ../archive-verification.json

验证器会检查 Manifest 状态、每个文件的存在性、文件大小、SHA-256、重复路径、额外文件和路径安全性，并确认 ZIP 内的 export-manifest.json 与外部清单完全一致。成功返回码为 0，验证失败返回码为 1；无法写入报告等命令错误返回码为 2。验证报告为 JSON，可用于后续归档验收。
