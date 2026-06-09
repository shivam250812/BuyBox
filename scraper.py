import csv
import random
import re
import asyncio
import os
import sys
import time
import requests
import subprocess
from playwright.async_api import async_playwright
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_file(filepath):
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        print("Skipping Telegram notification - missing bot token or chat ID in .env file.")
        return

    # Support multiple comma-separated chat IDs
    chat_ids = [cid.strip() for cid in TELEGRAM_CHAT_ID.split(',') if cid.strip()]
    
    for chat_id in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
            with open(filepath, 'rb') as f:
                files = {'document': f}
                data = {'chat_id': chat_id, 'caption': f"Amazon Buy Box Report: {time.strftime('%Y-%m-%d %H:%M:%S')}"}
                response = requests.post(url, files=files, data=data)
                
            if response.status_code == 200:
                print(f"Report file {filepath} sent successfully to Telegram chat {chat_id}!")
            else:
                print(f"Failed to send to Telegram chat {chat_id}. Status: {response.status_code}, Response: {response.text}")
        except Exception as e:
            print(f"Error sending file to Telegram chat {chat_id}: {e}")

def load_asins_from_csv(filename="input.csv"):
    asins = []
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if row:
                    val = row[0].strip()
                    # Skip header if it exists
                    if val and val.upper() != "ASIN":
                        asins.append(val)
    except FileNotFoundError:
        print(f"Error: {filename} not found. Please create {filename} with your ASINs.")
    return asins

TARGET_SELLER = "Bargad Healthcare"

async def scrape_asin(context, asin, results):
    max_retries = 3
    for attempt in range(max_retries):
        page = await context.new_page()
        
        # Block images to make it faster
        await page.route("**/*", lambda route: route.abort() if route.request.resource_type == "image" else route.continue_())
        
        url = f"https://www.amazon.com/dp/{asin}"
        if attempt == 0:
            print(f"\n--- Scraping ASIN: {asin} ---")
        else:
            print(f"\n--- Retrying ASIN: {asin} (Attempt {attempt + 1}/{max_retries}) ---")
        
        try:
            # Increased timeout to 60000ms (60 seconds)
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            # Slow Down Method: Random delay between 10 and 15 seconds to look more human
            await page.wait_for_timeout(random.randint(10000, 15000))
            
            # Check for CAPTCHA
            if "captcha" in page.url or await page.locator("form[action='/errors/validateCaptcha']").count() > 0:
                print(f"⚠️ [{asin}] Captcha detected! Attempting to automatically click 'Continue'...")
                
                # Attempt to auto-click the Continue / Submit button
                try:
                    submit_button = page.locator("form[action='/errors/validateCaptcha'] button[type='submit'], form[action='/errors/validateCaptcha'] input[type='submit'], button:has-text('Continue shopping'), a:has-text('Continue shopping')").first
                    if await submit_button.count() > 0:
                        await submit_button.click()
                        await page.wait_for_timeout(3000)
                except Exception:
                    pass
                    
                # Verify if the auto-click worked
                if "captcha" in page.url or await page.locator("form[action='/errors/validateCaptcha']").count() > 0:
                    print(f"⚠️ [{asin}] Auto-click failed or requires typing! Please solve it manually in the browser.")
                    # Wait until the url no longer contains captcha
                    try:
                        await page.wait_for_url(f"**/{asin}**", timeout=120000) 
                        print(f"[{asin}] Captcha solved, continuing...")
                        await page.wait_for_timeout(3000)
                    except Exception:
                        print(f"[{asin}] Timed out waiting for captcha to be solved.")
                        results.append({"ASIN": asin, "Status": "Skip - Captcha unresolved", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": ""})
                        return
                else:
                    print(f"[{asin}] Auto-click succeeded! Continuing...")
            
            # Extract Price
            price_locator = page.locator('#corePriceDisplay_desktop_feature_div .a-price .a-offscreen, #corePrice_feature_div .a-price .a-offscreen, .a-price .a-offscreen').first
            if await price_locator.count() == 0:
                price_locator = page.locator('.a-price-whole').first
                if await price_locator.count() == 0:
                    price_locator = page.locator('#priceblock_ourprice, #priceblock_dealprice').first
            
            if await price_locator.count() == 0:
                print(f"[{asin}] No price found -> Skip (Likely out of stock or unavailable)")
                results.append({"ASIN": asin, "Status": "Skip - No price", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": "Likely out of stock or unavailable"})
                return
                
            price_text = (await price_locator.inner_text()).strip()
            # Clean price string
            clean_price_str = re.sub(r'[^\d.]', '', price_text)
            if not clean_price_str:
                print(f"[{asin}] Invalid price format -> Skip")
                results.append({"ASIN": asin, "Status": "Skip - Invalid price", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": ""})
                return
                
            try:
                price_val = float(clean_price_str)
            except ValueError:
                print(f"[{asin}] Could not parse price '{price_text}' -> Skip")
                results.append({"ASIN": asin, "Status": "Skip - Could not parse price", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": ""})
                return

            # Extract Seller
            seller_text = ""
            main_buybox = page.locator('#merchant-info, #tabular-buybox').first
            if await main_buybox.count() > 0:
                seller_text = (await main_buybox.inner_text()).strip()
                
            if not seller_text or seller_text.isspace():
                seller_locator = page.locator('#sellerProfileTriggerId').first
                if await seller_locator.count() > 0:
                    seller_text = (await seller_locator.inner_text()).strip()
                
            if not seller_text:
                print(f"[{asin}] No seller found (No buy box) -> Skip")
                results.append({"ASIN": asin, "Status": "Skip - No seller / No buy box", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": ""})
                return
            
            # Compare seller name
            if TARGET_SELLER.lower() in seller_text.lower():
                print(f"[{asin}] Seller is {TARGET_SELLER} -> No problem")
                results.append({
                    "ASIN": asin, "Status": "OK", "Fetched Price": price_val, "Seller": seller_text, "New Price": "", "Remark": "No problem"
                })
            else:
                new_price = price_val - 0.03
                print(f"[{asin}] Seller is '{seller_text}' -> Reducing price by 0.03")
                results.append({
                    "ASIN": asin, "Status": "Adjusted", "Fetched Price": price_val, "Seller": seller_text, "New Price": round(new_price, 2), "Remark": "Seller is other, adjusted price"
                })
                
            # If we made it to the end successfully (either OK, Adjusted, or Skip), we break out of the retry loop
            return
                
        except Exception as e:
            # We catch the timeout or crash here!
            print(f"[{asin}] Error during scraping attempt {attempt + 1}: {e}")
            if attempt == max_retries - 1:
                # If this was the last attempt, we give up and record the error
                results.append({"ASIN": asin, "Status": f"Error", "Fetched Price": "", "Seller": "", "New Price": "", "Remark": "Max retries reached: " + str(e)})
            else:
                # Give the VPS a quick 5 second breather before the next retry
                await asyncio.sleep(5)
            
        finally:
            await page.close()

import platform

async def scrape_amazon_async(asins):
    results = []
    
    print("Starting Playwright to scrape Amazon...")
    async with async_playwright() as p:
        
        # Auto-detect local Chrome to avoid Playwright install issues
        executable_path = None
        system = platform.system()
        if system == "Darwin":
            mac_path = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            if os.path.exists(mac_path):
                executable_path = mac_path
        elif system == "Windows":
            win_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
            ]
            for path in win_paths:
                if os.path.exists(path):
                    executable_path = path
                    break
        
        launch_kwargs = {"headless": False}
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
            
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800}
        )
        
        # We need a setup page to set the pincode first
        setup_page = await context.new_page()
        # Block images on setup page too
        await setup_page.route("**/*", lambda route: route.abort() if route.request.resource_type == "image" else route.continue_())
        
        print("Setting delivery pincode to 11002...")
        try:
            await setup_page.goto("https://www.amazon.com/")
            await setup_page.wait_for_timeout(3000)
            
            location_link = setup_page.locator('#nav-global-location-popover-link')
            if await location_link.count() > 0:
                await location_link.click()
                await setup_page.wait_for_timeout(2000)
                
                pincode_input = setup_page.locator('#GLUXZipUpdateInput')
                if await pincode_input.count() > 0:
                    await pincode_input.fill("11002")
                    await setup_page.locator('#GLUXZipUpdate').click()
                    await setup_page.wait_for_timeout(2000)
                    
                    await setup_page.goto("https://www.amazon.com/")
                    await setup_page.wait_for_timeout(2000)
            print("Pincode setup completed.")
        except Exception as e:
            print(f"Warning: Could not set pincode automatically: {e}")
            print("Please set the pincode manually in the browser window within the next 10 seconds.")
            await setup_page.wait_for_timeout(10000)
        finally:
            await setup_page.close()
            
        # Limit to 2 concurrent tabs
        semaphore = asyncio.Semaphore(2)
        
        async def sem_scrape(asin):
            async with semaphore:
                await scrape_asin(context, asin, results)
                
        tasks = [sem_scrape(asin) for asin in asins]
        await asyncio.gather(*tasks)
        
        await browser.close()
        
    # Write to CSV
    if results:
        csv_filename = 'amazon_prices.csv'
        fieldnames = ["ASIN", "Status", "Fetched Price", "Seller", "New Price", "Remark"]
        try:
            with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(results)
            print(f"\nData successfully saved to {csv_filename}")
            
            # Save adjusted prices to a separate file for Seller Central
            adjusted_results = [r for r in results if r.get('Status') == 'Adjusted' and r.get('New Price')]
            if adjusted_results:
                adj_filename = 'adjusted_prices.csv'
                with open(adj_filename, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(adjusted_results)
                print(f"Saved {len(adjusted_results)} price adjustments to {adj_filename}")
                
                # Automatically trigger sellercentral.py to update these prices!
                print("\n[Auto-Trigger] Launching sellercentral.py to apply new prices...")
                # Use sys.executable to ensure we use the same python/venv
                subprocess.run([sys.executable, "sellercentral.py"])
                print("[Auto-Trigger] sellercentral.py finished execution.\n")
            
            # Send the file to Telegram
            await asyncio.to_thread(send_telegram_file, csv_filename)
        except Exception as e:
            print(f"Failed to save CSV: {e}")
    else:
        print("\nNo data scraped.")

if __name__ == "__main__":
    print("Initializing 24/7 scraping mode...")
    while True:
        asins_to_scrape = load_asins_from_csv("input.csv")
        if asins_to_scrape:
            print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Loaded {len(asins_to_scrape)} ASINs from input.csv")
            try:
                asyncio.run(scrape_amazon_async(asins_to_scrape))
            except Exception as e:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] A critical error occurred (Browser closed or crashed): {e}")
                print("The script will sleep and try again in the next cycle.")
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] No ASINs found to scrape. Please ensure input.csv has data.")
        
        # Wait 1 hour (3600 seconds) before running again
        print("\nFinished scraping cycle. Sleeping for 1 hour before next run...")
        time.sleep(3600)
