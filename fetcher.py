"""核心抓取模块：Playwright 登录（验证码识别）+ 提取费用页面余额。

多用户化：所有平台凭据由调用方通过 conf 传入（见表头字段），
不再依赖全局配置，实现「每用户按自己的账号/模型配置抓取」。

一次抓取流程：
  1. 打开登录页（conf.lbqh_base_url）
  2. 截图验证码 → 调用用户自己的 MaaS 多模态模型识别（conf.maas_*）
  3. 填入账号密码验证码 → 提交，失败则刷新验证码重试
  4. 点击菜单「费用」→ 定位「余额」元素 → 解析 余额/总支出/本月支出
"""
import asyncio
import re

import httpx
from playwright.async_api import Page, async_playwright


def recognize_captcha(b64_png: str, conf: dict) -> str | None:
    """调用用户的 MaaS 模型识别 4 位验证码。conf: {maas_api_url, maas_api_key, captcha_model}"""
    b64 = b64_png.split(",", 1)[1]
    payload = {
        "model": conf["captcha_model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "这是一张4位验证码图片，可能包含数字和字母，请只输出这4个字符，不要任何其他内容",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
    }
    resp = httpx.post(
        conf["maas_api_url"],
        headers={"Authorization": f"Bearer {conf['maas_api_key']}"},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


async def _captcha_src(page: Page) -> str | None:
    """从页面里找验证码图片的 base64 src。"""
    srcs = await page.locator("img").evaluate_all("els => els.map(e => e.src)")
    for s in srcs:
        if s.startswith("data:image/png;base64,"):
            return s
    return None


async def _refresh_captcha(page: Page) -> None:
    """尝试点击验证码图片刷新。"""
    try:
        await page.locator("img").nth(-1).click(timeout=1500)
    except Exception:
        pass
    await page.wait_for_timeout(800)


async def login(page: Page, conf: dict, max_attempts: int = 6) -> bool:
    """执行登录，返回是否成功。conf 需含 lbqh_user/lbqh_password/lbqh_base_url。"""
    await page.goto(f"{conf['lbqh_base_url']}/#/login", wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(3000)
    for _ in range(max_attempts):
        src = await _captcha_src(page)
        if not src:
            await _refresh_captcha(page)
            continue
        code = recognize_captcha(src, conf)
        if not code:
            await _refresh_captcha(page)
            continue
        await page.fill("#username", conf["lbqh_user"])
        await page.fill("#password", conf["lbqh_password"])
        await page.fill("#captchaCode", code)
        await page.click(".login-submit-button")
        await page.wait_for_timeout(3500)
        if "login" not in page.url:
            return True
        await page.fill("#captchaCode", "")
        await _refresh_captcha(page)
    return False


async def parse_fee_page(page: Page) -> dict:
    """点击「费用」后，从「余额」元素的父容器解析三个数值。"""
    await page.get_by_text("费用", exact=True).click()
    await page.wait_for_timeout(4000)
    el = page.locator("text=余额").first
    container = el.locator("xpath=../..")
    text = (await container.inner_text()).strip()
    nums = re.findall(r"(\d+(?:\.\d+)?)", text)
    if len(nums) < 3:
        raise RuntimeError(f"费用页面结构异常，解析出的数字不足: {text!r}")
    return {
        "balance": float(nums[0]),
        "total_spent": float(nums[1]),
        "month_spent": float(nums[2]),
    }


async def fetch_balance(conf: dict) -> dict:
    """完整流程：登录 → 提取余额，返回 {balance, total_spent, month_spent, fetched_at}。

    conf 必须含：lbqh_user / lbqh_password / lbqh_base_url / maas_api_url /
                 maas_api_key / captcha_model
    """
    from datetime import datetime  # noqa: PLC0415 —— 局部导入避免顶层依赖开销

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            if not await login(page, conf):
                raise RuntimeError("登录失败：验证码多次识别错误或账号密码有误")
            data = await parse_fee_page(page)
        finally:
            await browser.close()
    data["fetched_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    return data


async def main_cli() -> None:
    """命令行手动跑一次（python -m fetcher），用于迁移前快速验证。"""
    from config import cfg  # noqa: PLC0415
    from crypto import decrypt  # noqa: PLC0415

    conf = {
        "lbqh_user": cfg.LBQH_USER,
        "lbqh_password": cfg.LBQH_PASSWORD,
        "lbqh_base_url": cfg.LBQH_BASE_URL,
        "maas_api_url": cfg.MAAS_API_URL,
        "maas_api_key": cfg.MAAS_API_KEY,
        "captcha_model": cfg.CAPTCHA_MODEL,
    }
    data = await fetch_balance(conf)
    for k, v in data.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    asyncio.run(main_cli())