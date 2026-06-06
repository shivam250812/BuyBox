import asyncio
import sys
import os

from playwright.async_api import async_playwright

# Shared Chrome profile setup
from chrome_profile import create_browser

TARGET_ASIN = "B0D2MHV9PK"
TARGET_PRICE = "280.00"

SELLER_CENTRAL_INVENTORY_URL = "https://sellercentral.amazon.com/myinventory/inventory"

async def update_price(page, asin, new_price):
    print(f"Checking login status...")
    
    await page.goto(SELLER_CENTRAL_INVENTORY_URL, timeout=90000, wait_until="domcontentloaded")
    await asyncio.sleep(5)
    
    current_url = page.url.lower()
    if "signin" in current_url or "ap/signin" in current_url:
        print("Seller Central not logged in. Triggering auto-login script...")
        try:
            from auto_login import amazon_auto_login
            success = await amazon_auto_login(page.context)
            if not success:
                print("Auto-login failed. Exiting.")
                sys.exit(1)
        except ImportError as e:
            print(f"ImportError while loading auto_login.py: {e}")
            sys.exit(1)
            
        # Refresh the page after successful login
        await page.goto(SELLER_CENTRAL_INVENTORY_URL, timeout=90000, wait_until="domcontentloaded")
        await asyncio.sleep(5)
        
    print("Seller Central login active!")
    
    # -------------------------------------------------
    # 1. SEARCH FOR ASIN
    # -------------------------------------------------
    print(f"Searching for ASIN: {asin}")
    
    search_box = page.locator("input[placeholder*='ASIN'], input[placeholder*='Search SKU']").first
    await search_box.wait_for(state="visible", timeout=30000)
    
    await search_box.click()
    try:
        await search_box.press("Meta+A")
    except:
        pass
    await search_box.press("Control+A")
    await search_box.press("Backspace")
    
    await search_box.fill(asin)
    await search_box.press("Enter")
    
    print("Waiting for search results to load...")
    await page.wait_for_timeout(5000)
    await page.wait_for_load_state("networkidle")
    
    # -------------------------------------------------
    # 2. LOCATE PRICE INPUT BOX
    # -------------------------------------------------
    print(f"Waiting for ASIN {asin} to appear in results...")
    
    # Wait for the ASIN text to physically appear on the screen
    asin_text = page.get_by_text(asin).first
    await asin_text.wait_for(state="visible", timeout=20000)
    
    print("ASIN found! Locating the price input box...")
    
    # Amazon uses Katana React Grids (divs instead of tr/td).
    # To find the specific Price cell, we find the deepest container element 
    # that contains the text "Minimum price" and "Maximum price".
    # This perfectly isolates the single "Price and shipping cost" cell!
    price_cell = page.locator("div, td").filter(has_text="Minimum price").filter(has_text="Maximum price").last
    
    # The cell contains 3 inputs (Price, Min, Max). The first one is the main Price!
    price_input = price_cell.locator("input").first
    
    await price_input.wait_for(state="visible", timeout=5000)
    
    # Fill the new price using raw keyboard typing to trick React!
    print(f"Entering new price: ${new_price}")
    await price_input.click()
    
    # Select all existing text and delete it
    try:
        await price_input.press("Meta+A")
    except:
        pass
    await price_input.press("Control+A")
    await price_input.press("Backspace")
    
    # Type it exactly like a human would
    await page.keyboard.type(str(new_price), delay=100)
    
    # Press Enter to confirm the value and trigger the sticky bar
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(3000) # Wait for the sticky bottom bar to pop up
    
    # -------------------------------------------------
    # 3. CLICK SAVE ALL
    # -------------------------------------------------
    print("Clicking 'Save all'...")
    # Use Playwright's extremely robust role finder
    save_all_btn = page.get_by_role("button", name="Save all").first
    
    try:
        await save_all_btn.wait_for(state="visible", timeout=8000)
        await save_all_btn.click()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)
        print(f"✅ Successfully updated price of {asin} to {new_price}!")
    except:
        print("❌ Could not find the 'Save all' button! The price might not have been edited properly.")

async def main():
    async with async_playwright() as p:
        context = await create_browser(p, require_helium=False)
        page = await context.new_page()
        
        try:
            await update_price(page, TARGET_ASIN, TARGET_PRICE)
        except Exception as e:
            print(f"An error occurred: {e}")
            
        print("\nClosing browser in 3 seconds...")
        await asyncio.sleep(3)
        await context.close()

if __name__ == "__main__":
    asyncio.run(main())