# Meridian 区域媒体证据分析服务

本服务供研究团队对本地提供的媒体观察记录进行地区、议题和受众比较。系统不抓取网页、不自动推断报道倾向，也不声称合成演示能代表真实群体。

来源登记绑定地区、媒体和允许词表；观察记录按来源和外部编号保留修订。两名研究员独立编码，分歧由第三人裁定。按地区、故事、议题和受众去重，报告显示分母、排除原因和原始证据数。

## 版本化词表与趋势口径

议题词表按版本注册（`POST /vocabularies`），每个版本保存各议题的定义、上位类别（parent）和解释，注册后不可变——更新词表不会抹掉旧版本的审核依据。来源可用 `POST /sources/{source_id}/topics` 更新允许的议题列表，历史判断不受影响。

每条人工判断（`POST /decisions`、`POST /adjudications`）必须显式声明所用词表版本（`vocabulary`），议题必须在该版本中有定义且在来源允许列表内。同一研究员可在同一修订上按不同词表版本各编码一次，旧稿因此能被新词表复审；裁定按词表版本分别进行。系统不会凭标题把旧标签自动分配到新细分类别。

趋势查询（`POST /reports` 等）必须显式选择口径（`basis`）：

- `legacy`：原口径。只统计在指定旧版词表（`vocabulary`）下作出的判断。
- `parent`：可兼容的上位类别。所有已定稿判断按各自版本的 parent 链汇总到根类别；同一故事有多个版本的判断时采用最新注册版本的判断。
- `fine`：细分类别。只统计在指定新版词表下复审完成的判断。

只有旧版判断、尚未按查询词表复审的观察记为 `pending_rereview`（尚待复审的旧稿），与 `uncoded`、`disputed` 一样排除在分母之外。报告为每个地区标出实际可用的故事分母（`story_count`）、待复审观察数（`pending_rereview`）和判断版本分布（`judgment_versions`）；故事行携带 `vocabularies`、`recorded_topics` 和 `mixed_basis`，证据导出（`/reports/export`）逐条给出 `basis_vocabulary`、`basis_topic` 与全部已定稿判断（`judgments`）。窗口对比（`/reports/compare-windows`）和地区对比（`/reports/compare-regions`）输出 `comparability`：`basis_mismatch`、`rereview_pending`、`judgment_basis_shift`、`mixed_judgment_basis`，帮助负责人在新旧趋势并列时分辨真实报道变化与编码口径变化。

发布质量门对细分类别报告中仍含未复审旧标签的情况给出可解释的限制（`unreviewed_legacy_labels`），此时只能先发布上位类别口径或完成复审。冻结报告内嵌当时词表定义快照（`vocabulary_definitions`），发布后继续按当时词表可复核；复审或更正造成的漂移通过 `/releases/{release_id}/compare` 与 `/releases/{release_id}/changes` 可见。发表在旧时段、词表更新后才到达的稿件按判断所用词表版本计入；观察更正后，旧修订上的裁定保留在历史中但不再对应活动稿件，新头修订需重新编码。

使用 `python -m meridian.api --db meridian.sqlite3` 启动 FastAPI。主要接口包括 `POST /vocabularies`、`POST /sources`、`POST /observations`、`POST /decisions`、`POST /adjudications`、`POST /batches`、`POST /reports` 和 `POST /releases`。采集窗口使用 `/frames`，导出使用 `/reports/export` 或 `/releases/{release_id}/export`。

运行 `python -m unittest discover -s tests -v`、`python -m compileall -q meridian` 和 `python -m meridian.demo`。数据库使用 SQLite；测试和演示使用临时本地数据库。
