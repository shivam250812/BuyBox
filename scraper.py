import csv
import random
import re
import asyncio
import os
import sys
import time
import requests
import subprocess
import json
from playwright.async_api import async_playwright
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_CSV_URL = os.getenv("GOOGLE_SHEET_CSV_URL")

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

def send_telegram_message(text):
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        return

    chat_ids = [cid.strip() for cid in TELEGRAM_CHAT_ID.split(',') if cid.strip()]
    
    for chat_id in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
            response = requests.post(url, data=data)
            if response.status_code != 200:
                print(f"Failed to send text to {chat_id}. Status: {response.status_code}, Response: {response.text}")
        except Exception as e:
            print(f"Error sending text to Telegram chat {chat_id}: {e}")

HISTORY_FILE = "history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=4)

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

def clean_price(text):
    if not text:
        return None
    text = text.replace(",", "")
    match = re.search(r"\d+\.?\d*", text)
    return float(match.group()) if match else None

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
            
            # =========================
            # Extract Price
            # =========================
            price_val = None

            # Primary selectors (strictly scoped to the main product display)
            price_selectors = [
                "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
                "#corePriceDisplay_desktop_feature_div .a-price",
                "#corePrice_desktop .a-price .a-offscreen",
                "#corePrice_desktop .a-price",
                "#corePrice_feature_div .a-price .a-offscreen",
                "#corePrice_feature_div .a-price",
                "#price_inside_buybox",
                "#newBuyBoxPrice .a-offscreen",
                "#newBuyBoxPrice",
                ".apexPriceToPay .a-offscreen",
                ".apexPriceToPay",
                "#priceblock_ourprice",
                "#priceblock_dealprice",
                "#buyBoxAccordion .a-price .a-offscreen",
                "#buyBoxAccordion .a-price"
            ]

            for selector in price_selectors:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() > 0:
                        text = (await locator.text_content()).strip()
                        if text:
                            price_val = clean_price(text)
                            if price_val:
                                print(f"[{asin}] Price found using selector: {selector} -> ${price_val}")
                                break
                except Exception:
                    pass

            # =========================
            # Final Validation
            # =========================

            if price_val is None:
                print(f"[{asin}] No price found -> Skip (Likely out of stock or unavailable)")
                results.append({
                    "ASIN": asin,
                    "Status": "Skip - No price",
                    "Fetched Price": "",
                    "Seller": "",
                    "New Price": "",
                    "Remark": "Likely out of stock or unavailable"
                })
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
                new_price = price_val - 0.50
                print(f"[{asin}] Seller is '{seller_text}' -> Reducing price by 0.50")
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
    
    # Split into batches of 100
    batch_size = 100
    batches = [asins[i:i + batch_size] for i in range(0, len(asins), batch_size)]
    print(f"Divided {len(asins)} ASINs into {len(batches)} batches of up to {batch_size} ASINs each.")
    
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
        
    for index, batch in enumerate(batches):
        print(f"\n--- Starting Batch {index + 1}/{len(batches)} (Size: {len(batch)}) ---")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 800}
            )
            
            # Setup Page for Postcode
            setup_page = await context.new_page()
            await setup_page.route("**/*", lambda route: route.abort() if route.request.resource_type == "image" else route.continue_())
            
            print(f"[{index + 1}] Setting delivery pincode to 10001 (New York)...")
            try:
                await setup_page.goto("https://www.amazon.com/", timeout=30000, wait_until="domcontentloaded")
                await setup_page.wait_for_timeout(3000)
                
                location_link = setup_page.locator('#glow-ingress-block, #nav-global-location-popover-link').first
                if await location_link.count() > 0:
                    await location_link.click()
                    await setup_page.wait_for_timeout(2000)
                    
                    pincode_input = setup_page.locator('#GLUXZipUpdateInput').first
                    if await pincode_input.count() > 0:
                        await pincode_input.fill("")
                        await pincode_input.type("10001", delay=80)
                        await setup_page.wait_for_timeout(1000)
                        
                        # Click Apply
                        apply_btn = setup_page.locator(
                            "#GLUXZipUpdate input[type='submit'], #GLUXZipUpdate .a-button-input, button:has-text('Apply'), input[aria-labelledby='GLUXZipUpdate-announce']"
                        ).first
                        if await apply_btn.count() > 0:
                            await apply_btn.click()
                            await setup_page.wait_for_timeout(2000)
                        
                        # Click Done/Continue to close popup
                        try:
                            done_btn = setup_page.locator(
                                "button[name='glowDoneButton'], #GLUXConfirmClose, button:has-text('Continue'), button:has-text('Done')"
                            ).first
                            if await done_btn.count() > 0:
                                await done_btn.click()
                                await setup_page.wait_for_timeout(1000)
                        except Exception:
                            pass
                            
                        # Verify pincode by reloading
                        await setup_page.goto("https://www.amazon.com/", timeout=30000, wait_until="domcontentloaded")
                        await setup_page.wait_for_timeout(2000)
                        
                        location_text = ""
                        try:
                            loc_el = setup_page.locator("#glow-ingress-line2").first
                            if await loc_el.count() > 0:
                                location_text = (await loc_el.inner_text()).strip()
                        except:
                            pass
                        
                        if "10001" in location_text or "New York" in location_text:
                            print(f"[{index + 1}] Pincode verified: {location_text}")
                        else:
                            print(f"[{index + 1}] WARNING: Pincode may not have been set correctly. Location shows: '{location_text}'")
                print(f"[{index + 1}] Pincode setup completed.")
            except Exception as e:
                print(f"[{index + 1}] Warning: Could not set pincode automatically: {e}")
            finally:
                await setup_page.close()
                
            # Limit concurrency to 2 concurrent tabs
            semaphore = asyncio.Semaphore(2)
            
            async def sem_scrape(asin):
                async with semaphore:
                    await scrape_asin(context, asin, results)
                    
            tasks = [sem_scrape(asin) for asin in batch]
            await asyncio.gather(*tasks)
            
            await browser.close()
            
        # Add a short delay between batches to be human-like
        if index < len(batches) - 1:
            print("Batch finished. Sleeping for 10 seconds before next batch...")
            await asyncio.sleep(10)
        
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
            
            # Record "OK" items into history
            history = load_history()
            for r in results:
                if r.get("Status") == "OK":
                    asin = r["ASIN"]
                    if asin not in history:
                        history[asin] = {}
                    history[asin]["has_buybox"] = True
                    history[asin]["price"] = r["Fetched Price"]
            save_history(history)
            
        except Exception as e:
            print(f"Failed to save CSV: {e}")
    else:
        print("\nNo data scraped.")

if __name__ == "__main__":
    print("Initializing 24/7 scraping mode...")
    cycle_count = 0
    while True:
        cycle_count += 1
        
        # Auto-Sync from Google Sheets
        if GOOGLE_SHEET_CSV_URL:
            print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Downloading latest ASINs from Google Sheets...")
            try:
                # Automatically convert Google Sheet links to CSV export links
                if "pubhtml" in GOOGLE_SHEET_CSV_URL:
                    csv_url = GOOGLE_SHEET_CSV_URL.replace("pubhtml", "pub?output=csv")
                elif "/edit" in GOOGLE_SHEET_CSV_URL:
                    csv_url = GOOGLE_SHEET_CSV_URL.split("/edit")[0] + "/export?format=csv"
                else:
                    csv_url = GOOGLE_SHEET_CSV_URL
                    
                response = requests.get(csv_url, timeout=15)
                if response.status_code == 200:
                    with open("input.csv", "w", encoding="utf-8") as f:
                        f.write(response.text)
                    print("Successfully updated input.csv from cloud!")
                else:
                    print(f"Failed to fetch Google Sheet. Status Code: {response.status_code}. Using existing input.csv.")
            except Exception as e:
                print(f"Error fetching Google Sheet: {e}. Using existing input.csv.")
                
        asins_to_scrape = load_asins_from_csv("input.csv")
        if asins_to_scrape:
            print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Started Cycle {cycle_count}. Loaded {len(asins_to_scrape)} ASINs from input.csv")
            try:
                asyncio.run(scrape_amazon_async(asins_to_scrape))
            except Exception as e:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] A critical error occurred (Browser closed or crashed): {e}")
                print("The script will sleep and try again in the next cycle.")
                
            # Send the 5-Cycle Report!
            if cycle_count % 5 == 0:
                print("Generating 5-Cycle Telegram Report...")
                history = load_history()
                report_lines = [f"<b>Buy Box Report (Cycle {cycle_count})</b>\n"]
                for asin in asins_to_scrape:
                    data = history.get(asin, {})
                    has_bb = data.get("has_buybox", False)
                    price = data.get("price", "Unknown")
                    status_text = "Have BuyBox" if has_bb else "Lost BuyBox"
                    report_lines.append(f"<b>{asin}</b> | {status_text} | Price: ${price}")
                
                report_text = "\n".join(report_lines)
                send_telegram_message(report_text)
                print("Report sent!")
                
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] No ASINs found to scrape. Please ensure input.csv has data.")
            
        if cycle_count >= 10:
            print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Reached maximum limit of 10 cycles. Shutting down scraper...")
            break
        
        # Wait 1 hour (3600 seconds) before running again
        print(f"\nFinished Cycle {cycle_count}. Sleeping for 1 hour before next run...")
        time.sleep(3600)
