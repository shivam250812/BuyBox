# sellercentral.py

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from playwright.async_api import async_playwright

# Shared Chrome profile setup
from chrome_profile import create_browser
from notifications import send_email_notification

# =========================================================
# CONFIG
# =========================================================

DEFAULT_INPUT_CSV = "input.csv"
DEFAULT_OUTPUT_CSV = "gated_output.csv"

SELLER_CENTRAL_URL = (
    "https://sellercentral.amazon.com/myinventory/inventory"
)

# =========================================================
# LOGIN CHECK
# =========================================================

async def ensure_logged_in(page):

    await page.goto(
        SELLER_CENTRAL_URL,
        timeout=90000,
        wait_until="domcontentloaded",
    )

    await asyncio.sleep(5)

    current_url = page.url.lower()

    if (
        "signin" in current_url
        or "ap/signin" in current_url
    ):
        print("\n  Seller Central not logged in. Triggering auto-login script...\n")
        
        try:
            from notifications import send_email_notification
            send_email_notification(
                subject="Amazon Scraper: Seller Central Login Required",
                message="Seller Central session expired. The script is now running the auto-login sequence automatically..."
            )
        except Exception:
            pass
            
        try:
            from auto_login import amazon_auto_login
            success = await amazon_auto_login(page.context)
            if not success:
                print(" Auto-login failed. Exiting.", file=sys.stderr)
                try:
                    send_email_notification(
                        subject="Amazon Scraper: Auto-Login Failed",
                        message="The automatic login attempt failed. Please check the VPS."
                    )
                except Exception:
                    pass
                sys.exit(1)
            else:
                try:
                    send_email_notification(
                        subject="Amazon Scraper: Auto-Login Successful",
                        message="The auto-login sequence completed successfully. The script is continuing."
                    )
                except Exception:
                    pass
        except ImportError as e:
            print(f" ImportError while loading auto_login.py: {e}", file=sys.stderr)
            sys.exit(1)
            
        # Refresh the page after successful login
        await page.goto(
            SELLER_CENTRAL_URL,
            timeout=90000,
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(5)

    print(" Seller Central login active\n")


# =========================================================
# CHECK SINGLE ASIN
# =========================================================

async def check_asin(page, asin):

    print(f" Checking {asin}")

    try:

        # -------------------------------------------------
        # OPEN PRODUCT SEARCH PAGE
        # -------------------------------------------------

        await page.goto(
            SELLER_CENTRAL_URL,
            timeout=90000,
            wait_until="domcontentloaded",
        )

        await asyncio.sleep(5)

        # -------------------------------------------------
        # FIND SEARCH BOX
        # -------------------------------------------------

        search_box = page.locator(
            "input[placeholder*='product title']"
        ).first

        await search_box.wait_for(
            state="visible",
            timeout=30000
        )

        # -------------------------------------------------
        # CLEAR OLD TEXT
        # -------------------------------------------------

        await search_box.click()

        try:
            await search_box.press("Meta+A")
        except:
            await search_box.press("Control+A")

        await search_box.press("Backspace")

        # -------------------------------------------------
        # TYPE ASIN
        # -------------------------------------------------

        await search_box.fill(asin)

        await asyncio.sleep(1)

        # -------------------------------------------------
        # PRESS ENTER
        # -------------------------------------------------

        await search_box.press("Enter")

        # -------------------------------------------------
        # WAIT FOR PRODUCT PANEL
        # -------------------------------------------------

        panel = page.locator(
            "div[slot='header']"
        ).first

        await panel.wait_for(
            state="visible",
            timeout=30000
        )

        await asyncio.sleep(3)

        # -------------------------------------------------
        # GET PAGE TEXT
        # -------------------------------------------------

        body_text = (
            await page.locator("body").inner_text()
        ).lower()

        button_texts = await page.locator(
            "button"
        ).all_inner_texts()

        buttons = " ".join(button_texts).lower()

        combined = body_text + "\n" + buttons

        # -------------------------------------------------
        # DETECT STATUS
        # -------------------------------------------------

        status = "UNKNOWN"
        message = ""

        # GATED
        if (
            "you need approval to list" in combined
            or "apply to sell" in combined
            or "request approval" in combined
            or "approval required" in combined
        ):

            status = "GATED"
            message = "Brand approval required"

        # RESTRICTED
        elif (
            "listing limitations apply" in combined
        ):

            status = "RESTRICTED"
            message = "Listing limitations apply"

        # NOT GATED
        elif (
            "sell this product" in combined
        ):

            status = "NOT_GATED"
            message = "Can sell"

        # PARTIAL
        elif (
            "copy listing" in combined
            and "sell this product" not in combined
        ):

            status = "PARTIAL_RESTRICTION"
            message = "Cannot directly sell"

        # -------------------------------------------------
        # EXTRACT TITLE
        # -------------------------------------------------

        title = ""

        try:

            title_locator = page.locator(
                "h4"
            ).first

            title = (
                await title_locator.inner_text()
            ).strip()

        except:
            pass

        print(f"   → {status}")

        return {
            "asin": asin,
            "title": title,
            "status": status,
            "message": message,
        }

    except Exception as e:

        print(f" {asin}: {e}")

        return {
            "asin": asin,
            "title": "",
            "status": "ERROR",
            "message": str(e),
        }


# =========================================================
# PUBLIC API (for run_pipeline.py)
# =========================================================

def read_input_csv(input_csv: str) -> list[str]:
    asins = []
    with open(input_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            asin = (
                row.get("ASIN")
                or row.get("asin")
                or row.get("Asin")
            )
            if asin:
                asins.append(asin.strip())
    return asins


async def run_seller_central(
    input_csv: str = DEFAULT_INPUT_CSV,
    output_csv: str = DEFAULT_OUTPUT_CSV,
) -> str:
    """
    Run Seller Central gating check for all ASINs in input_csv.
    Returns path to the output CSV.
    """
    asins = read_input_csv(input_csv)

    if not asins:
        print(f" No ASINs found in {input_csv}")
        return output_csv

    # RESUME LOGIC: Check existing output_csv
    processed_asins = set()
    file_exists = os.path.exists(output_csv)
    if file_exists:
        try:
            with open(output_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    asin = row.get("ASIN", "").strip()
                    if asin:
                        processed_asins.add(asin)
        except Exception:
            file_exists = False

    asins_to_process = [a for a in asins if a not in processed_asins]

    if not asins_to_process:
        print(" All ASINs already processed. Nothing to do.")
        return output_csv

    if processed_asins:
        print(f" Resuming... {len(processed_asins)} already processed. {len(asins_to_process)} left to check.")

    async with async_playwright() as p:

        context = await create_browser(p, require_helium=False)

        page = await context.new_page()

        await ensure_logged_in(page)

        mode = "a" if file_exists else "w"
        with open(
            output_csv,
            mode,
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.writer(f)

            if not file_exists:
                writer.writerow([
                    "ASIN",
                    "TITLE",
                    "STATUS",
                    "MESSAGE",
                ])

            for asin in asins_to_process:

                result = await check_asin(
                    page,
                    asin
                )

                writer.writerow([
                    result["asin"],
                    result["title"],
                    result["status"],
                    result["message"],
                ])

                f.flush()

        print("\n Done")
        print(f" Output saved: {output_csv}")

        await context.close()

    return output_csv


# =========================================================
# CLI ENTRY POINT
# =========================================================

def _parse_args():
    parser = argparse.ArgumentParser(description="Seller Central gating checker")
    parser.add_argument(
        "--input-csv",
        default=DEFAULT_INPUT_CSV,
        help=f"Input CSV with ASIN column (default: {DEFAULT_INPUT_CSV})",
    )
    parser.add_argument(
        "--output-csv",
        default=DEFAULT_OUTPUT_CSV,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT_CSV})",
    )
    return parser.parse_args()


async def main():
    args = _parse_args()
    await run_seller_central(args.input_csv, args.output_csv)


if __name__ == "__main__":
    asyncio.run(main())