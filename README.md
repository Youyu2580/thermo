# 资金温度计

量价口径的行情/风格判定页，每个交易日北京时间 15:35 由 GitHub Actions 自动更新。

- 页面：`site/index.html`（由 `update.py` 生成的单文件，无外部依赖）
- 数据源：腾讯行情为主、东方财富为备；失败会在 Actions 日志里报 `[warn]`
- 手动触发：Actions → daily-thermo → Run workflow
