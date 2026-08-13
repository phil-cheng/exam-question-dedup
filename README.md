# 试题文义查重

对**同一份**标准试题 Excel 做近重复查重：找出换了说法、但仍是同一道题的题对。不落库，关掉即丢。

检索文本 = 题干 + 非空选项 A–F。答案、题型不参与计算。

## 怎么用

### 直接跑 exe（推荐）

1. 打开 `dist/QuestionDedup.exe`（文件名用英文，避免 Windows 打包乱码；窗口标题仍是中文）
2. 没有模板时点「下载模板」，另存内置的 `template.xls`
3. 选择填好的、含工作表 **「正式题目」** 的 `.xls` / `.xlsx`
4. （可选）填写向量服务地址和模型名，例如 `http://127.0.0.1:11434/v1` + `bge-m3`
5. 点「开始查重」
6. 拖动「相似度」滑条过滤，导出当前题对为 xlsx

没有向量服务、或服务连不上时，自动只用 BM25。

### 从源码跑

```powershell
pip install -r requirements.txt
python main.py
```

命令行验收（不弹界面）：

```powershell
python main.py --cli test.xls
python main.py --cli test.xls --threshold 0.82
```

## Excel 模板

必须有工作表 **「正式题目」**，表头须含 `编号`、`试题内容`。  
选项列名为 `候选项A` … `候选项F`（可空）。标准空表是根目录 `template.xls`（界面「下载模板」即此文件）；带题样例是 `test.xls`。

不符合模板会直接报错，用来校验导入文件。

## 配置

把 `config.example.json` 拷到程序旁，改名为 `config.json`。界面里点「保存配置」也会写出这个文件。

| 字段 | 含义 | 是否给用户看 |
|---|---|---|
| `embed_base_url` | OpenAI 兼容地址：本地 Ollama/vLLM，或硅基流动等在线服务 | 界面可填 |
| `embed_model` | 模型名 | 界面可填 |
| `embed_api_key` | 在线服务必填；本地 Ollama 可空。界面密文显示 | 界面可填 |
| `semantic_weight` | 有向量时综合分里语义占比，默认 `0.7`（其余 0.3 给 BM25） | **不暴露**，改文件即可 |

开发时配置写在项目根；打包后写在 exe 同一目录。

综合分：

- 有向量：`s = 0.7 × 余弦 + 0.3 × BM25归一化`
- 无向量：`s = BM25(相对自己)`

滑条只卡 `s`，改阈值不重算。`semantic_weight` 不是滑条。

## 检索在做什么

1. 字 2/3-gram + BM25，每题 Top50  
2. 若配置了 embedding，批量打向量，精确余弦再取 Top50  
3. 两路并集作为候选，写出每对的原始分  
4. 滑条按综合分过滤并导出题对  

设计说明见 `docs/`：

- [需求说明](docs/需求.md)
- [为何混合 embedding 与 BM25](docs/为何混合embedding与BM25.md)
- [为何不整段清洗检索文本](docs/为何不整段清洗检索文本.md)
- [如何选择 embedding 模型](docs/如何选择embedding模型.md)
- [自研 vs seekdb](docs/方案对比-自研与seekdb.md)

## 再打包

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1
```

产物：`dist/QuestionDedup.exe`。exe 名必须用 ASCII，否则 PyInstaller 在中文 Windows 下会写成乱码。

## 目录

```
app/                 读表、分词、BM25、远程向量、融合、界面
docs/                需求与方案讨论
scripts/build_exe.ps1
dist/QuestionDedup.exe
test.xls             样例题
```
