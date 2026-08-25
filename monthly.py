"""按月抓取：登录 → 选月份 → 翻页抓全月消费明细 → 聚合入库。

多用户化：平台凭据由 conf 传入（lbqh_user/lbqh_password/lbqh_base_url/maas_*），
数据按 user_id 入库隔离。

消费明细一行示例（费用页表格列顺序）：
  支付方 | 消费方 | 计费模式 | 资源类型 | API密钥名称 | 资源名称 | 配置描述 | 开始时间 | 结束时间 | 费用(元) | 支付完成
"""
import asyncio
import re
import sys
from datetime import datetime

from playwright.async_api import Page, async_playwright

from fetcher import login
from storage import (
    init_db,
    rebuild_monthly_stats,
    replace_month_details,
)

# token 正则：兼容「文本输入：N Tokens」「文本输出：N Tokens」「命中输入：N Tokens」及无标签形式
TOKEN_PAT = re.compile(
    r"((?:文本输入|文本输出|命中输入)[：:]?\s*)?([\d,]+)\s*Tokens"
)


def parse_desc(desc: str) -> tuple[int, int, int, int]:
    """解析「配置描述」，返回 (文本输入, 文本输出, 命中输入, 未分类token)。"""
    text_in = text_out = hit_in = bare = 0
    for mm in TOKEN_PAT.finditer(desc or ""):
        n = int(mm.group(2).replace(",", ""))
        tag = mm.group(1)
        if tag and "文本输入" in tag:
            text_in += n
        elif tag and "文本输出" in tag:
            text_out += n
        elif tag and "命中输入" in tag:
            hit_in += n
        else:
            bare += n
    return text_in, text_out, hit_in, bare


def _num(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return 0.0


def parse_row(cells: list[str]) -> dict | None:
    """解析一行明细，返回 dict 或 None（表头/空行）。"""
    if len(cells) < 11:
        return None
    model = cells[5].strip()
    if not model or model in ("暂无数据", "--"):
        return None
    text_in, text_out, hit_in, bare = parse_desc(cells[6])
    return {
        "api_key_name": cells[4].strip(),
        "model": model,
        "text_in_tokens": text_in,
        "text_out_tokens": text_out,
        "hit_in_tokens": hit_in,
        "bare_tokens": bare,
        "total_tokens": text_in + text_out + hit_in + bare,
        "fee": _num(cells[9]),
        "start_time": cells[7].strip(),
        "end_time": cells[8].strip(),
        "billing_status": cells[10].strip(),
    }


async def select_month(page: Page, year: int, month: int) -> None:
    """通过月份面板选择指定年/月（Ant Design 月份选择器）。"""
    await page.locator("input[placeholder='请选择月份']").click()
    await page.wait_for_timeout(1200)
    year_btn = page.locator(".ant-picker-year-btn")
    current_year = int((await year_btn.inner_text()).replace("年", "").strip())
    diff = current_year - year
    btn = (
        ".ant-picker-header-super-prev-btn" if diff > 0
        else ".ant-picker-header-super-next-btn"
    )
    for _ in range(abs(diff)):
        await page.locator(btn).first.click()
        await page.wait_for_timeout(400)
    cell = page.locator(f"td.ant-picker-cell[title='{year:04d}-{month:02d}']")
    if await cell.count() == 0:
        raise RuntimeError(f"月份面板中找不到 {year:04d}-{month:02d}")
    await cell.first.click()
    await page.wait_for_timeout(3500)


async def _page_rows(page: Page) -> list[dict]:
    """抓当前页 tbody 中的有效明细行。"""
    rows: list[dict] = []
    n = await page.locator("table tbody tr").count()
    for i in range(n):
        cells = [c.strip() for c in await page.locator("table tbody tr").nth(i).locator("td").all_inner_texts()]
        r = parse_row(cells)
        if r:
            rows.append(r)
    return rows


def _row_key(r: dict) -> tuple:
    """行的业务唯一键：整行内容，跨页重叠的同内容行才视为重复。"""
    return (
        r["api_key_name"], r["model"], r["start_time"], r["end_time"],
        r["total_tokens"], round(r["fee"], 6),
    )


async def extract_month(page: Page) -> list[dict]:
    """抓当前所选月份的全部页明细（自动翻页直到最后一页）。"""
    details: list[dict] = []
    seen_keys: set = set()
    seen = 0
    total_text = await page.locator(".ant-pagination-total-text").inner_text(timeout=5000)
    total = int(re.search(r"(\d+)", total_text).group(1))
    for pg in range(1, 999):
        rows = await _page_rows(page)
        new = [r for r in rows if _row_key(r) not in seen_keys]
        for r in new:
            seen_keys.add(_row_key(r))
        details.extend(new)
        seen += len(rows)
        next_btn = page.locator(".ant-pagination-next")
        disabled = await next_btn.get_attribute("aria-disabled") == "true"
        if disabled or seen >= total:
            break
        await next_btn.locator("button, a, .ant-pagination-item-link").first.click()
        await page.wait_for_timeout(2500)
    return details


async def _fetch_with_browser(browser, conf: dict, year: int, month: int) -> list[dict]:
    """在给定 browser 上完成「登录→选月→翻页抓全月」并返回明细。"""
    page = await (await browser.new_context(viewport={"width": 1600, "height": 1200})).new_page()
    try:
        if not await login(page, conf):
            raise RuntimeError("登录失败：验证码多次识别错误或账号密码有误")
        await page.get_by_text("费用", exact=True).click()
        await page.wait_for_timeout(4000)
        await select_month(page, year, month)
        return await extract_month(page)
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def fetch_month(year: int, month: int, conf: dict, user_id: int, browser=None) -> list[dict]:
    """抓取指定月份全部明细并入库，返回明细列表。

    conf: {lbqh_user, lbqh_password, lbqh_base_url, maas_api_url, maas_api_key, captcha_model}
    可复用传入的 browser（若为 None 则新建并自行关闭）。
    """
    init_db()
    if browser is None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                details = await _fetch_with_browser(browser, conf, year, month)
            finally:
                await browser.close()
    else:
        details = await _fetch_with_browser(browser, conf, year, month)

    fetched_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    replace_month_details(f"{year:04d}-{month:02d}", details, fetched_at, user_id)
    rebuild_monthly_stats(f"{year:04d}-{month:02d}", fetched_at, user_id)
    return details


async def main_cli() -> None:
    """命令行手动跑一次（python -m monthly [YYYY-MM] [N]）。使用 .env 旧配置（迁移前调试用）。"""
    from config import cfg  # noqa: PLC0415
    from crypto import decrypt  # noqa: PLC0415

    args = sys.argv[1:]
    now = datetime.now()
    if args:
        y, m = map(int, args[0].split("-"))
        months_back = int(args[1]) if len(args) > 1 else 1
    else:
        y, m, months_back = now.year, now.month, 1
    targets = []
    yy, mm = y, m
    for _ in range(months_back):
        targets.append((yy, mm))
        mm -= 1
        if mm == 0:
            mm, yy = 12, yy - 1

    conf = {
        "lbqh_user": cfg.LBQH_USER,
        "lbqh_password": cfg.LBQH_PASSWORD,
        "lbqh_base_url": cfg.LBQH_BASE_URL,
        "maas_api_url": cfg.MAAS_API_URL,
        "maas_api_key": cfg.MAAS_API_KEY,
        "captcha_model": cfg.CAPTCHA_MODEL,
    }
    for year, month in targets:
        label = f"{year:04d}-{month:02d}"
        try:
            details = await fetch_month(year, month, conf, user_id=1)
            fee = sum(d["fee"] for d in details)
            tok = sum(d["total_tokens"] for d in details)
            print(f"[{label}] 明细 {len(details)} 条 | 费用 {fee:.4f} 元 | tokens {tok:,}")
        except Exception as e:  # noqa: BLE001
            print(f"[{label}] 抓取失败: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main_cli())