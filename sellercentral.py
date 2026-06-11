import asyncio
import sys
import os
import csv
import re

from playwright.async_api import async_playwright

# Shared Chrome profile setup
from chrome_profile import create_browser

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
    # 2. CHECK AVAILABLE FBM QUANTITY
    # -------------------------------------------------
    # Wait for the ASIN text to physically appear on the screen before proceeding
    asin_text = page.get_by_text(asin).first
    await asin_text.wait_for(state="visible", timeout=20000)
    
    print("ASIN found! Locating the Inventory cell...")
    # Find the deepest container that has "Available", "(FBM)", AND an input field!
    inventory_cell = page.locator("div, td").filter(has_text="Available").filter(has_text="(FBM)").filter(has=page.locator("input")).last
    inventory_input = inventory_cell.locator("input").first
    
    await inventory_input.wait_for(state="visible", timeout=5000)
    qty_str = await inventory_input.input_value()
    
    try:
        available_qty = int(qty_str.strip())
    except ValueError:
        available_qty = 0
        
    print(f"Available (FBM) Quantity: {available_qty}")
    
    if available_qty <= 0:
        print(f"Quantity is {available_qty}. Skipping price update for {asin}.")
        return
        
    # -------------------------------------------------
    # 3. LOCATE PRICE INPUT BOX
    # -------------------------------------------------
    print("Quantity > 0! Locating the price input box...")
    
    # Amazon uses Katana React Grids (divs instead of tr/td).
    # We find the deepest container that contains "Minimum price", "Maximum price", and an input field.
    # This perfectly isolates the single "Price and shipping cost" cell!
    price_cell = page.locator("div, td").filter(has_text="Minimum price").filter(has_text="Maximum price").filter(has=page.locator("input")).last
    
    # The cell contains 3 inputs (Price, Min, Max). The first one is the main Price!
    price_input = price_cell.locator("input").first
    
    await price_input.wait_for(state="visible", timeout=5000)
    
    # -------------------------------------------------
    # NEW DYNAMIC PRICE LOGIC
    # -------------------------------------------------
    sc_price_str = await price_input.input_value()
    try:
        sc_price_val = float(re.sub(r'[^\d.]', '', sc_price_str))
    except ValueError:
        print(f"Could not parse SC Price '{sc_price_str}'. Defaulting to Adjusted Price.")
        sc_price_val = float(new_price)
        
    adjusted_price_val = float(new_price)
    
    if sc_price_val <= adjusted_price_val:
        final_target = round(sc_price_val - 0.50, 2)
        print(f"SC Price (${sc_price_val}) is <= Adjusted Target (${adjusted_price_val}). Undercutting SC by 0.50 -> Final Target: ${final_target}")
    else:
        final_target = adjusted_price_val
        print(f"Adjusted Target (${adjusted_price_val}) is lower than SC Price (${sc_price_val}). Using Adjusted Target -> Final Target: ${final_target}")
        
    final_price_str = f"{final_target:.2f}"
    
    # Fill the new price using raw keyboard typing to trick React!
    print(f"Entering new price: ${final_price_str}")
    await price_input.click()
    
    # Select all existing text by triple-clicking it (most reliable way)
    await price_input.click(click_count=3)
    await page.keyboard.press("Backspace")
    
    # Also use fill("") just in case to ensure it's completely empty
    await price_input.fill("")
    
    # Type it exactly like a human would
    await page.keyboard.type(final_price_str, delay=100)
    
    # Click the inventory cell to force the price input to lose focus!
    # Clicking another interactable cell in the React Grid is the most reliable way 
    # to trigger the Katana 'Save all' sticky bar.
    await inventory_cell.click()
    await page.wait_for_timeout(3000) # Wait for the sticky bottom bar to pop up
    
    # -------------------------------------------------
    # 3. CLICK SAVE ALL
    # -------------------------------------------------
    print("Clicking 'Save all'...")
    # Use a raw text locator to find the button no matter what kind of HTML tag it is!
    save_all_btn = page.locator("text='Save all'").last
    
    try:
        await save_all_btn.wait_for(state="visible", timeout=8000)
        await save_all_btn.click()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)
        print(f" Successfully updated price of {asin} to {new_price}!")
        
        # Save exact updated price to history.json
        import json
        history_file = "history.json"
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = {}
            
        if asin not in history:
            history[asin] = {}
        history[asin]["has_buybox"] = False
        history[asin]["price"] = final_target
        
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=4)
            
    except:
        print(" Could not find the 'Save all' button! The price might not have been edited properly.")

def load_adjusted_prices(filename="adjusted_prices.csv"):
    items = []
    if not os.path.exists(filename):
        print(f"File {filename} not found.")
        return items
    
    with open(filename, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            asin = row.get("ASIN", "").strip()
            new_price = row.get("New Price", "").strip()
            if asin and new_price:
                items.append((asin, new_price))
    return items

async def main():
    items = load_adjusted_prices()
    if not items:
        print("No adjusted prices to process. Exiting sellercentral.py")
        return
        
    print(f"Loaded {len(items)} items to update from adjusted_prices.csv")
    
    async with async_playwright() as p:
        context = await create_browser(p, require_helium=False)
        page = await context.new_page()
        
        for asin, new_price in items:
            try:
                print(f"\n======================================")
                print(f"Processing ASIN: {asin} | Target Price: ${new_price}")
                await update_price(page, asin, new_price)
            except Exception as e:
                print(f"An error occurred while updating {asin}: {e}")
            
        print("\nAll updates finished. Closing browser in 3 seconds...")
        await asyncio.sleep(3)
        await context.close()

if __name__ == "__main__":
    asyncio.run(main())