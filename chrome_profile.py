import asyncio

async def create_browser(p, require_helium=False, is_setup_mode=False):
    # Launch a persistent Chromium context for Seller Central
    return await p.chromium.launch_persistent_context(
        user_data_dir="./chrome_data",
        headless=False,
        args=["--disable-blink-features=AutomationControlled"]
    )
