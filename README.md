# Public Holiday Tool

免费版中国大陆公共假日判断脚本，固定使用 iCloud holiday `.ics` feed：

```text
https://calendars.icloud.com/holidays/cn_zh.ics/
```

它会：

- 拉取 `.ics`
- 解析 `VEVENT`
- 展开常见 `RRULE`
- 处理全天事件的 `DTSTART` / `DTEND` 日期范围
- 优先识别 `X-APPLE-SPECIAL-DAY`
- 返回 `is_public_holiday` 和 `is_working_day`

## Usage

在 Smart 里选择 **Based on Python Script**，使用 `holiday_tool.py`。

核心函数：

```python
def main(input_data: dict[str, Any] | None = None) -> dict[str, Any]:
    ...
```

`input_data` 可传：

```json
{
  "date": "2026-05-07"
}
```

也兼容：

```json
{
  "target_date": "2026-05-07"
}
```

不传 `date` 或 `target_date` 时，会按 `Asia/Beijing` 当前日期判断今天。

## Output

```json
{
  "date": "2026-05-07",
  "is_public_holiday": false,
  "is_working_day": true,
  "holiday_name": null
}
```

## Classification Rules

优先级从高到低：

1. `X-APPLE-SPECIAL-DAY:ALTERNATE-WORKDAY`：补班日，`is_public_holiday=false`，`is_working_day=true`
2. `X-APPLE-SPECIAL-DAY:WORK-HOLIDAY`：放假日，`is_public_holiday=true`，`is_working_day=false`
3. `SUMMARY` 包含 `（班）` 或 `(班)`：补班日
4. `SUMMARY` 包含 `（休）` 或 `(休)`：放假日
5. 没有命中特殊事件时，按中国大陆周末规则判断 `is_working_day`
