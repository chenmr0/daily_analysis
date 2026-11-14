# AlphaEvo 隔离实验脚本（重建版）

**这不是 daily_analysis 的产品实现、集成模块或发布功能。** 本目录仅提供独立实验入口，不改应用代码、默认配置、主分支和交易行为，不接券商，不调用付费模型。该目录位于独立实验分支。

## 来源与重建边界

依赖外部项目 [ZhuLinsen/alphaevo](https://github.com/ZhuLinsen/alphaevo)，固定提交 `712e60b3a79fa3835ac725e91e4f5b2c5811f652`，Apache-2.0。这里是自写驱动脚本，未复制其产品实现；上游许可证、声明由单独检出保留。

2026-11-13 曾进行平安银行公开行情、上游美股快照和日期契约试验。临时工作区后来丢失，**本目录是在2026-11-14按调查记录重新整理的代码，不是恢复的原文件，也不是原始结果包**。原行情、原数据库、原日志及哈希均未发布。新脚本的严格日期裁剪与原试验不同，不能声称逐字节重现旧结果。

原试验观察仅作历史背景：默认30信号门槛因单股仅1信号而停止；降低门槛的机制试验生成v2，但未改善。它们不是本目录的新测试结果，不能据此推断投资收益。

## 环境

请使用独立Python 3.12环境，不安装进 daily_analysis 的生产环境：

```bash
# 从仓库根运行；依赖源码和虚拟环境均放到仓库外
export LAB="$HOME/alphaevo-lab"
mkdir -p "$LAB"
git clone https://github.com/ZhuLinsen/alphaevo.git "$LAB/alphaevo"
git -C "$LAB/alphaevo" checkout 712e60b3a79fa3835ac725e91e4f5b2c5811f652
python3 -m venv "$LAB/venv"
"$LAB/venv/bin/pip" install -r experiments/alphaevo/requirements-tested.txt
"$LAB/venv/bin/pip" install -e "$LAB/alphaevo"
git -C "$LAB/alphaevo" rev-parse HEAD
```

脚本使用上游内部API，只对该固定提交验证。`requirements-tested.txt` 是本次环境版本记录，不保证所有操作系统均适用。

## 1. 本地公开行情：生成、迭代、留出回测

自行准备有使用权限的CSV，列为 `date,open,high,low,close,volume`，日期建议ISO格式、成交量口径一致。脚本不联网取数，不上传数据，不自动读取DSA数据库或`.env`。不应将私人持仓或账户数据用作输入。

```bash
"$LAB/venv/bin/python" experiments/alphaevo/run_trial.py \
  --csv /path/to/public-prices.csv --symbol 000001 --market a_share \
  --source-label '实际数据来源与下载日期' --adjustment '实际复权方式或unknown' \
  --train-start 2023-01-01 --train-end 2025-12-31 \
  --test-start 2026-01-01 --test-end 2026-11-13 \
  --output "$LAB/results/default"
```

- 默认信号门槛30，最多3轮；不足会停止，**停止不等于程序未运行**。
- 如仅检查变异机制，可另用全新输出目录加 `--min-signals 1`，明确属于低门槛冒烟，不是有效性验收。
- 按训练/测试窗口严格裁剪数据，测试数据不参与冠军选择。为避免上游预热区间可能产生窗口外交易，本版不向引擎提供窗口外预热行，故窗口开头会损失指标预热样本。这是实验隔离策略，不是上游修复。
- 固定单只证券；移除草稿股票池过滤，不虚构基本面。
- `param_search` 是上游按反馈调整参数的路径，不是全部无密钥能力，更不是LLM自主研究。
- 输出包含策略YAML、回测JSON、来源标签、输入SHA256、数据库。输出目录必须全新，避免覆盖旧试验。不要把数据库或整份日志提交到Git。
- 未核验复权、分红、交易税费、停牌/涨跌停或ETF类别规则；默认成本模型不是完整实盘费用模型。

## 2. 多候选优化机制

```bash
"$LAB/venv/bin/python" experiments/alphaevo/check_optimizer.py \
  --output "$LAB/results/optimizer" --max-candidates 12
```

使用上游自带、标记来源为yfinance的固定美股五股快照，不下载实时行情，也不是A股测试。分别调用 `ParamOptimizer` 和 `ExitOptimizer`，保留排名。没有独立留出集，最佳候选仅是同样本排名，不构成泛化证明。本目录不重新分发上游行情快照。

可在上游检出中另运行 `alphaevo showcase`。注意其委员会是规则检查器，六步变异计划预先定义，不能把演示说成自主发现。

## 3. 历史日期契约缺陷复现

```bash
"$LAB/venv/bin/python" experiments/alphaevo/repro_history_dates.py
```

无需AKShare或网络API。固定时钟2026-11-13，执行原版DataManager和AkShareAdapter，仅替换底层数据接口为合成日历行，断言四个旧版本行为：

1. 请求2024年一季度，实际向供应商请求2026-05-17至11-13，最终报空。
2. 请求2026年1至4月，静默仅返回4月4至30日。
3. 近端区间正常返回。
4. 首源非空但日期不符时，不调用已有正确历史的备用源，且健康状态记为成功。

这些是**缺陷特征断言**，不是正确行为验收；上游修复后断言应当失败，需要更新测试。夹具含周末，每行不是实际交易日或真实行情。

## 新版验证

```bash
"$LAB/venv/bin/python" -m unittest discover -s experiments/alphaevo -p 'test_*.py' -v
```

实际执行结果见 `VALIDATION.md`。不把旧记录和新验证混报。

## 发布与数据边界

仅发布脚本、依赖清单和简短说明，不含行情CSV、模型密钥、账户数据、研究数据库、虚拟环境、大日志或上游完整代码。原始公开行情的再分发许可没有核实，故不上传。实验命令不需要模型密钥。

此目录未改变任何产品接口，因此不修改产品变更日志或生产配置。说明仅中文，是面向本次实验交付，不变更现有双语产品文档。

仅用于工程研究，不构成投资建议，不承诺策略收益。
