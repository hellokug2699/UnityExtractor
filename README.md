# UnityExtractor

面向 Unity 游戏的证据驱动扫描与通用资源检查工具。项目采用模块化设计，将 Unity 容器解析与具体游戏的数据解释分离。

> 当前状态：**P0 已完成，P1 对象枚举开发中**。P0 可扫描 APK、ZIP、目录和独立文件；P1 通过可选 UnityPy 后端枚举 SerializedFile / AssetBundle 中的对象。当前尚不导出资源文件。

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

## P1：对象枚举（开发中）

安装 UnityPy 后：

```bash
unity-extractor /path/to/game.apk --enumerate-objects --format json --output objects.json
unity-extractor /path/to/game.bundle --enumerate-objects
```

也可以扫描解包后的游戏目录。默认单个候选容器最大为 512 MiB，可通过 `--max-container-mb` 调整。

P1 当前输出对象类型、名称（可读取时）、Path ID、字节大小、容器来源、容器内路径和部分 SerializedFile 元数据。对象名称可能重复，因此对象身份由来源、容器和 Path ID 共同确定。单个对象名称或容器路径读取失败时，会保留对象记录并附加 warning。

APK / ZIP 输入只会选择具有 Unity Bundle 或 SerializedFile 头部特征的成员，使用随机临时文件交给解析后端，并在处理后清理；不会按压缩包内路径直接写入文件系统。P1 仍有单容器、总容器字节数、容器数量和对象数量限制。

**P1 目前属于首轮实现，尚未完成真实游戏对象枚举验收。** UnityPy 解析失败或不支持的格式会记录错误，不会被宣称为已解析。

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
