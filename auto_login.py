import asyncio
import os
import time
from dotenv import load_dotenv
from playwright.async_api import async_playwright
import pyotp
import sys

# Load env before importing local modules
load_dotenv()

# Import the shared chrome profile launcher
try:
    from chrome_profile import create_browser
except ImportError:
    print("Error: chrome_profile.py not found. Please run this script from the project root.")
    sys.exit(1)

AMAZON_EMAIL = os.getenv("AMAZON_EMAIL", "")
AMAZON_PASSWORD = os.getenv("AMAZON_PASSWORD", "")
AMAZON_TOTP_SECRET = os.getenv("AMAZON_TOTP_SECRET", "")

async def handle_merchant_selection(page):
    """Helper to select Shudhit and United States if the screen appears."""
    select_btn = page.locator("button:has-text('Select Account'), button:has-text('Select')").first
    if await select_btn.count() > 0 and await select_btn.is_visible():
        print("-> On Merchant Picker screen. Selecting Shudhit -> United States...")
        
        # 1. Click Shudhit
        shudhit_btn = page.get_by_text("Shudhit", exact=False).first
        if await shudhit_btn.count() > 0 and await shudhit_btn.is_visible():
            await shudhit_btn.click()
            await page.wait_for_timeout(1500)
            
        # 2. Click United States
        us_btn = page.get_by_text("United States", exact=False).first
        if await us_btn.count() > 0 and await us_btn.is_visible():
            await us_btn.click()
            await page.wait_for_timeout(1500)
            
        # 3. Click Select Account
        print("   => Clicking 'Select Account'...")
        await select_btn.click()
        
        # VERY IMPORTANT: Wait for the navigation to the dashboard to finish!
        await page.wait_for_timeout(8000)
        return True
    return False

async def fill_otp(page):
    """Helper to fill OTP if the screen is present."""
    totp_input = page.locator("input[id='auth-mfa-otpcode']")
    if await totp_input.count() > 0 and await totp_input.is_visible():
        print("   => 2FA requested. Generating TOTP code...")
        if not AMAZON_TOTP_SECRET:
            print("      ERROR: AMAZON_TOTP_SECRET is missing from .env!")
            return False
            
        totp = pyotp.TOTP(AMAZON_TOTP_SECRET)
        current_otp = totp.now()
        print(f"      => Generated OTP: {current_otp}")
        
        await totp_input.fill(current_otp)
        
        remember_device = page.locator("input[name='rememberDevice']")
        if await remember_device.count() > 0:
            print("      => Checking 'Don't require OTP on this browser'...")
            await remember_device.check()
            
        await page.locator("input#auth-signin-button").click()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)
        return True
    return False

async def amazon_auto_login(context):
    print("\n--- Starting Amazon Seller Central Auto-Login ---")
    page = await context.new_page()
    
    try:
        from notifications import send_email_notification
    except ImportError:
        def send_email_notification(subject, message): pass
    
    login_action_taken = False
    
    try:
        print("1. Navigating to Seller Central...")
        await page.goto("https://sellercentral.amazon.in", timeout=60000)
        await page.wait_for_timeout(4000)
        
        # State machine loop: check what screen we are on and handle it
        for step in range(10):  # max 10 steps to prevent infinite loop
            current_url = page.url.lower()
            
            # 1. Are we on the Merchant Selection screen?
            if await handle_merchant_selection(page):
                if not login_action_taken:
                    login_action_taken = True
                    send_email_notification(
                        subject="Amazon Scraper: Auto-Login Triggered",
                        message="Seller Central requires action (Merchant selection). The script is handling it automatically..."
                    )
                continue
                
            # 2. Are we on the Dashboard?
            # If the screen shows Shudhit and NO 'Select' button, we are fully logged in
            select_btn = page.locator("button:has-text('Select Account'), button:has-text('Select')").first
            if await select_btn.count() == 0 or not await select_btn.is_visible():
                if "dashboard" in current_url or await page.locator("text='Home'").count() > 0 or await page.get_by_text("Shudhit", exact=False).count() > 0:
                    print("=> Reached Amazon Seller Central Dashboard! 'Shudhit | United States' is visible. You are fully logged in.")
                    
                    if login_action_taken:
                        send_email_notification(
                            subject="Amazon Scraper: Auto-Login Successful",
                            message="The script has successfully auto-logged into Amazon Seller Central and is now continuing the scrape!"
                        )
                    return True
                
            # If we reached here, it means we are NOT on the dashboard. 
            # If this is the first step, we are definitely logged out.
            if not login_action_taken:
                login_action_taken = True
                send_email_notification(
                    subject="Amazon Scraper: Seller Central Login Required",
                    message="Seller Central session expired. The script is now running the auto-login sequence automatically..."
                )
                
            # 3. Are we on the OTP screen?
            if await fill_otp(page):
                continue

            # 4. Sometimes there's a landing page with a "Log in" button
            if await page.locator("a:has-text('Log in')").count() > 0 and await page.locator("a:has-text('Log in')").first.is_visible():
                print("-> Clicking 'Log in' landing page button...")
                await page.locator("a:has-text('Log in')").first.click()
                await page.wait_for_load_state("networkidle")
                continue

            # 5. Email Form?
            email_input = page.locator("input[type='email'], input[name='email']")
            if await email_input.count() > 0 and await email_input.first.is_visible():
                print(f"-> Filling email: {AMAZON_EMAIL}")
                await email_input.first.fill(AMAZON_EMAIL)
                
                # If there's a continue button (split login), click it
                continue_btn = page.locator("input#continue")
                if await continue_btn.count() > 0 and await continue_btn.first.is_visible():
                    await continue_btn.first.click()
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_timeout(2000)
                    continue # Loop around to check for password form
                
            # 6. Password Form?
            pass_input = page.locator("input[type='password'], input[name='password']")
            if await pass_input.count() > 0 and await pass_input.first.is_visible():
                print("-> Filling password...")
                await pass_input.first.fill(AMAZON_PASSWORD)
                
                keep_signed_in = page.locator("input[name='rememberMe']")
                if await keep_signed_in.count() > 0:
                    await keep_signed_in.check()
                    
                print("-> Submitting login...")
                await page.locator("input#signInSubmit").click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(3000)
                continue
                
            # If we get here and none of the above matched, we might just need to wait a bit
            await page.wait_for_timeout(3000)
            
        print("\n❌ Error: Could not verify login after 10 steps. Stuck on unknown page.")
        if login_action_taken:
            send_email_notification(
                subject="Amazon Scraper: Auto-Login Failed",
                message="The script attempted to auto-login to Amazon Seller Central but got stuck. Please log into your VPS and check the Chrome window."
            )
        return False

    except Exception as e:
        print(f"\n❌ Error during Amazon auto-login: {e}")
        if login_action_taken:
            send_email_notification(
                subject="Amazon Scraper: Auto-Login Failed",
                message=f"The auto-login script encountered an error: {e}"
            )
        try:
            await page.wait_for_timeout(30000)
        except Exception:
            pass
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def main():
    if not AMAZON_EMAIL or not AMAZON_PASSWORD:
        print("Error: AMAZON_EMAIL or AMAZON_PASSWORD missing from .env")
        sys.exit(1)

    async with async_playwright() as p:
        print("Launching persistent Chrome profile...")
        # We don't require Helium here, just need the profile loaded
        context = await create_browser(p, require_helium=False, is_setup_mode=True)
        
        await amazon_auto_login(context)
        
        print("\nClosing browser...")
        await context.close()

if __name__ == "__main__":
    asyncio.run(main())
