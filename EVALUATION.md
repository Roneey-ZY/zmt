# 自动评估与测试记录

## 本次运行

Windows，Python 3.12.14。规则模式计时覆盖读取三份单证、标准化、审核及在内存生成 Excel。基准包含 60 组按 10 种场景构造的合成交易，混用 CSV、XLSX 和文本 PDF。

| 指标 | 规则模式实测 | 真实模型模式 |
|---|---:|---|
| 字段提取准确率 | 100%（1,932 / 1,932） | 待测 |
| 异常识别精确率 | 100%（42 / 42） | 待测 |
| 异常识别召回率 | 100%（42 / 42） | 待测 |
| 待复核提示精确率 / 召回率 | 100% / 100%（18 项） | 待测 |
| 端到端成功率 | 100%（60 / 60） | 待测 |
| 单次审核平均 / 中位耗时 | 21.045 ms / 19.812 ms | 待测 |
| 自动化测试 | 18 / 18 通过 | 含 SDK 测试模型集成 |

字段准确率按预期字段与提取字段并集统计，缺失、错值和额外字段计错。异常按「规则、分类、SKU」多重集比对，待复核单独统计。端到端成功需要字段与发现均正确且报告已生成。测试使用合成模板，结果仅反映已覆盖场景。字段位置由解析和报告测试额外核验。

初版评估中，Agents SDK 测试由测试模型驱动 SDK 执行真实 Python 工具并验证结果。测试夹具只在测试代码使用。初版评估运行时未配置模型密钥，真实模型表现和耗时留空。报告生成逻辑在两种执行模式下共享。

本机 Edge 以 1366 × 768 窗口执行了案例 B、实际 Excel 下载、无凭据时切回规则模式、案例 A 的三种文件上传和重新打开。早期自动化运行中出现过刷新后 15 秒内未渲染，最终两次重开与刷新复测均通过。公网和中国大陆网络尚未验收。

## 复现

```powershell
python -m pip install -r requirements-dev.txt
python -m scripts.generate_demo_data --benchmark
python -m pytest -q --junitxml=data/test-results.xml
python -m scripts.evaluate --mode both --output data/evaluation.json
```

需要快速检查真实模型时，先配置 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`，再用 `--mode both --limit 10` 覆盖各类场景。CLI 与网页均支持环境变量和项目的 Streamlit Secrets。运行完整对照会发出多次付费模型请求。

[data/evaluation.json](data/evaluation.json) 保存逐组期望、实际发现、字段差异、工具事件、耗时和错误。[data/test-results.xml](data/test-results.xml) 保存 pytest 结果。当前基准评估中没有失败案例。

## DeepSeek 实测（2026 年 11 月 10 日）

用 `deepseek-flash` 在 Streamlit 应用中运行案例 B。四个工具按顺序成功执行，检出三项业务差异和一项逻辑矛盾，生成的 Excel 含四项发现和证据页。模型在追问中正确说明净重 120 kg 大于毛重 110 kg。应用内审核用时 7.38 秒。另在 SDK 请求层模拟 DeepSeek HTTP 503，确认页面显示失败且可对同一批单证切回规则模式。

以下对照使用相同的前 10 组合成基准。计时包括单证读取、审核和 Excel 生成，AI 侧另含网络和模型耗时。

| 指标 | 规则模式 | DeepSeek 模式 |
|---|---:|---:|
| 端到端通过 | 10/10 | 10/10 |
| 预设异常检出 | 7/7，0 误报 | 7/7，0 误报 |
| 预设复核提示检出 | 3/3，0 额外提示 | 3/3，0 额外提示 |
| 平均耗时 | 21.401 ms | 10.304 s |
| 中位耗时 | 17.587 ms | 10.431 s |

两种模式共享解析器和审核规则。本次对照反映模型调度的完成情况和开销，陌生模板的读取表现需另行评估。DeepSeek 接入后的本地回归测试通过 19 项。

[10 组对照原始记录](data/evaluation-deepseek.json) · [案例 B 运行日志](data/demo-B-deepseek.json) · [案例 B Excel](data/demo-B-deepseek.xlsx) · [模拟失败回退](data/deepseek-fallback.json)

## 陌生表头确认版

同一份原始单证分别进行常规审核和确认表头后复审。输入、标准答案和测试脚本保存在项目中。评估程序使用预先记录的正确字段对应关系模拟用户确认。真实 DeepSeek 建议单独比较，其结果未写入标准答案。

| 合成样本 | 完整通过 | 预设异常检出 | 复审误报 |
|---|---:|---:|---:|
| 原 5 组独立盲测复测 | 3/5 → 5/5 | 1/5 → 5/5 | 0 |
| 新造 5 组布局 | 1/5 → 5/5 | 0/5 → 5/5 | 0 |

- 新布局覆盖陌生英文 CSV 表头、中文 XLSX 表头、双页无边框文本 PDF、真实重量缺失和应忽略的可选列。两份缺失重量样本复审后都保留待复核。
- 真实 DeepSeek 对三张合成发票给出 12 个陌生字段建议，12 个均与预设答案一致。该结果是三张样本上的建议准确性，业务结论由用户确认后的规则复审产生。
- 常规 60 组基准复跑全部通过，预设异常 42/42，零误报。新版自动化测试共 28 项。
- 本机 Edge 实跑案例 D 的模型授权、建议、确认、复审与 Excel 下载，并单独跑通案例 E 的无边框 PDF 确认。案例 D 的 [实机下载报告](data/case-D-confirmed-report.xlsx) 保留第 6 列的表头确认记录。
- 此次未测真实企业模板的总体表现及人工确认耗时。

```powershell
python -m scripts.generate_template_validation
python -m scripts.evaluate_template_mapping
python -m scripts.evaluate_template_mapping --live-ai
python -m scripts.evaluate --mode rules --output data/evaluation-v2.json
python -m pytest -q
```

[表头确认逐组结果](data/template-evaluation.json) · [60 组新版回归结果](data/evaluation-v2.json) · [新版自动化测试记录](data/test-results-v2.xml)


## 登录保护回归（2026-11-11）

实测完整 pytest：32 项全部通过，耗时 9.78 秒。记录见 `data/test-results-login.xml`。新增 4 项测试覆盖哈希与配置校验、会话过期/轮换、跨会话共享的进程内限速、配置缺失时拦截，以及 Streamlit AppTest 实际页面登录失败/成功、案例 B 四项异常及报告生成、退出清理。此测试未进行新的模型实网调用，云平台登录配置与公网验收尚未完成。
