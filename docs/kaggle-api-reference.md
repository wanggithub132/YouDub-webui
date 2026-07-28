# Kaggle 官方 API 接口使用文档

> 本文档整理自 Kaggle 官方文档（2026-02 版本），只覆盖本项目自动化流水线用到的部分。
>
> **官方源地址（对照阅读用）：**
>
> | 内容 | 官方地址 |
> |---|---|
> | 文档总入口 | <https://github.com/Kaggle/kaggle-api/blob/main/docs/README.md> |
> | Kernels 命令详解 | <https://github.com/Kaggle/kaggle-api/blob/main/docs/kernels.md> |
> | Datasets 命令详解 | <https://github.com/Kaggle/kaggle-api/blob/main/docs/datasets.md> |
> | kernel-metadata.json 字段 | <https://github.com/Kaggle/kaggle-api/blob/main/docs/kernels_metadata.md> |
> | dataset-metadata.json 字段 | <https://github.com/Kaggle/kaggle-api/blob/main/docs/datasets_metadata.md> |
> | 网页版文档入口 | <https://www.kaggle.com/docs/api> |
> | CLI 源码仓库 | <https://github.com/Kaggle/kaggle-api> |
>
> 注：GitHub 页面打不开时，把 `github.com/Kaggle/kaggle-api/blob/main` 换成
> `raw.githubusercontent.com/Kaggle/kaggle-api/main` 可看纯文本版。

## 安装

```bash
pip install kaggle    # 需要 Python 3.11+
```

## 一、认证（4 种方式）

| 方式 | 用法 | 本项目是否使用 |
|---|---|---|
| OAuth 网页授权 | `kaggle auth login` | ❌ |
| 新版 API Token | 环境变量 `KAGGLE_API_TOKEN=xxx` | ❌ |
| Token 文件 | 存到 `~/.kaggle/access_token` | ❌ |
| **Legacy 凭证** | 环境变量 `KAGGLE_USERNAME` + `KAGGLE_KEY`（或 `~/.kaggle/kaggle.json`） | ✅ GitHub Actions 里用的就是这个 |

Legacy 凭证获取方式：kaggle.com → Settings → API → "Legacy API Credentials" →
"Create Legacy API Key"，会下载 `kaggle.json`（内含 username 和 key）。

⚠️ 本项目踩坑记录：Kaggle CLI 必须使用 Legacy API Key，新版 token 格式不兼容
`KAGGLE_USERNAME`/`KAGGLE_KEY` 环境变量注入方式。

## 二、Kernels 命令（跑代码）

### kaggle kernels push —— 上传并立即运行

```bash
kaggle kernels push -p <目录> [--accelerator <ID>] [-t <秒>]
```

- 目录里必须有：代码文件（`.py`/`.ipynb`）+ `kernel-metadata.json`
- kernel 已存在则更新，不存在则新建；推送后 Kaggle 自动开始运行
- `--accelerator <ID>`：指定加速器型号（与 metadata 的 `machine_shape` 字段等效）
- `-t, --timeout <秒>`：最大运行时长，超时强杀
  （本项目用 `--timeout 19800` = 5.5 小时，防止 Actions 轮询结束后 kernel 继续空烧 GPU 配额）

**2026 年 2 月可用加速器 ID：**

| ID | 说明 |
|---|---|
| `NvidiaTeslaP100` | 默认 GPU（不指定时抽到的往往是它） |
| `NvidiaTeslaT4` | **T4 x2，本项目锁定使用** |
| `NvidiaTeslaT4Highmem` | T4 高内存版 |
| `NvidiaL4` / `NvidiaL4X1` | L4 |
| `TpuV38` / `Tpu1VmV38` / `TpuV5E8` / `TpuV6E8` | TPU 系列 |
| `NvidiaTeslaA100` / `NvidiaH100` / `NvidiaRtxPro6000` | 仅特定比赛参与者 / Kaggle 管理员可用 |

### kernel-metadata.json 字段说明

```json
{
  "id": "用户名/kernel-slug",
  "title": "kernel 标题",
  "code_file": "gpu_runner.py",
  "language": "python",
  "kernel_type": "script",
  "is_private": true,
  "enable_gpu": true,
  "enable_internet": true,
  "machine_shape": "NvidiaTeslaT4",
  "dataset_sources": ["用户名/数据集slug"],
  "competition_sources": [],
  "kernel_sources": [],
  "model_sources": []
}
```

| 字段 | 说明 |
|---|---|
| `id` | URL slug，`用户名/kernel-slug`；slug 与 title 联动（title 小写、空格转 `-`） |
| `code_file` | 代码文件路径（相对 metadata 文件） |
| `language` | `python` / `r` / `rmarkdown` |
| `kernel_type` | `script` / `notebook` |
| `is_private` | 不填默认 `true` |
| `enable_gpu` | 是否用 GPU，不填默认 `false` |
| `enable_internet` | 是否联网，不填默认 `false` |
| `machine_shape` | ⭐ 加速器型号（见上表），**从源头锁定 T4、避免抽到 P100** |
| `dataset_sources` | 挂载的数据集列表，运行时出现在 `/kaggle/input/<数据集名>/` |

### kaggle kernels status —— 查运行状态

```bash
kaggle kernels status <用户名/kernel-slug>
```

返回最新一次运行状态：`running` / `complete` / `error` / `cancel*`。
本项目 Actions 每 60 秒轮询一次，最多约 5.3 小时。

### kaggle kernels output —— 下载运行输出

```bash
kaggle kernels output <用户名/kernel-slug> -p <本地目录> [选项]
```

- 下载的是 kernel 运行结束后 `/kaggle/working/` 里的文件
- `--file-pattern <正则>`：只下匹配的文件，如 `".*\.zip$"`
- `-o` 强制覆盖；`--page-size` 每页文件数（最大 200）

### 其他 kernels 命令

| 命令 | 用途 |
|---|---|
| `kaggle kernels init -p <目录>` | 生成 metadata 模板 |
| `kaggle kernels pull <slug> -p <目录> -m` | 拉取 kernel 代码和 metadata |
| `kaggle kernels list -m` | 列出自己的 kernel |
| `kaggle kernels files <slug>` | 列出 kernel 输出文件名（不下载） |
| `kaggle kernels delete <slug> -y` | 删除 kernel（不可恢复） |

### Kaggle Secrets（密钥管理）

- **只能在网页 Notebook 编辑器里配置**（Add-ons → Secrets），**无 CLI 支持**
- 代码里读取：`from kaggle_secrets import UserSecretsClient` →
  `UserSecretsClient().get_secret("KEY")`
- ⚠️ 本项目结论：API 推送的 script kernel 不适用此机制，
  密钥继续走"GitHub Secrets → Actions 占位符注入私有 kernel"方案。

## 三、Datasets 命令（存模型缓存 / 产物归档）

### kaggle datasets create —— 新建数据集

```bash
kaggle datasets create -p <目录> [-r zip] [--public]
```

- 目录里放数据文件 + `dataset-metadata.json`
- `-r, --dir-mode`：子目录处理方式 —— `skip`（默认，忽略子目录）/ `zip`（压缩上传）/ `tar`
- 默认私有，`--public` 才公开

`dataset-metadata.json` 最小示例：

```json
{
  "title": "youdub-outputs",
  "id": "用户名/youdub-outputs",
  "licenses": [{"name": "CC0-1.0"}]
}
```

### kaggle datasets version —— 发布新版本

```bash
kaggle datasets version -p <目录> -m "版本说明" [-r zip] [-d]
```

- ⚠️ **每个版本都是全量快照，不是增量**：目录里没带的文件在新版本中会消失
- `-d, --delete-old-versions`：删除旧版本。
  因为当前版本已含全部文件，删旧版**不丢数据**，但能防止存储量按版本数翻倍
  （本项目产物归档必带此参数）
- `-m` 为必填

### kaggle datasets download —— 下载

```bash
kaggle datasets download <用户名/slug> -p <目录> [--unzip] [-f <单个文件>]
```

- `--unzip`：下载后自动解压并删掉 zip
- `-f <文件名>`：只下载指定文件

### 其他 datasets 命令

| 命令 | 用途 |
|---|---|
| `kaggle datasets status <slug>` | 查创建/更新状态（`ready` = 可用） |
| `kaggle datasets files <slug> --page-size 200` | 只列文件名不下载（可做去重查询） |
| `kaggle datasets list -m` | 列出自己的数据集 |
| `kaggle datasets metadata <slug> -p <目录>` | 下载 metadata 文件 |
| `kaggle datasets delete <slug> -y` | 删除数据集（不可恢复） |

## 四、本项目的用法速查

| 环节 | 命令/字段 | 位置 |
|---|---|---|
| 锁定 T4 | metadata `machine_shape: "NvidiaTeslaT4"` | `.github/workflows/kaggle-gpu.yml` 组装 kernel 步骤 |
| kernel 硬超时 | `kaggle kernels push --timeout 19800` | 同上，推送步骤 |
| 轮询状态 | `kaggle kernels status`（每 60s，最多 320 次） | 同上 |
| 取产物 | `kaggle kernels output` | 同上 |
| 挂载模型缓存 + 去重依据 | `dataset_sources: [youdub-models, youdub-outputs]` | kernel metadata |
| 产物归档 | `kaggle datasets version --dir-mode zip --delete-old-versions` | 归档步骤 |
| 时间三层保险 | 软截止 4h40m（脚本内）→ Actions 轮询 5.3h → kernel 硬超时 5.5h | `gpu_runner.py` + workflow |
