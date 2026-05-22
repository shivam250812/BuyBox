# Amazon BuyBox Scraper

An asynchronous Amazon scraper built with Python and Playwright. It reads a list of ASINs, automatically sets the delivery pincode (to 110021 by default), and checks if the seller for the Buy Box matches a target seller ("Bargad Healthcare"). If the seller doesn't match, it automatically calculates a new price with a slight reduction.

## Features
- **Asynchronous Scraping**: Processes up to 2 tabs concurrently to maximize speed without being overly aggressive.
- **Image Blocking**: Blocks image loading during scraping to minimize bandwidth and decrease page load time.
- **CAPTCHA Pausing**: Automatically pauses and allows the user to manually solve any CAPTCHAs that appear in the browser before continuing.

## Requirements
- Python 3.7+
- Google Chrome installed locally

## Installation

1. Clone the repository:
```bash
git clone https://github.com/shivam250812/BuyBox.git
cd BuyBox
```

2. (Optional) Create and activate a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
```

3. Install the required dependencies:
```bash
pip install -r requirements.txt
```

## Usage

1. Create an `input.csv` file in the root directory. Add your ASINs to this file (one ASIN per row).
2. Run the script:
```bash
python3 scraper.py
```
3. The script will open a Google Chrome window, set the pincode, and then start scraping the ASINs in batches of 2.
4. The final results will be saved in `amazon_prices.csv`.

## Configuration
Inside `scraper.py`, you can modify:
- `TARGET_SELLER`: The name of the seller you want to check against.
- The default pincode (currently set to `110021`).
- The concurrency limit (`asyncio.Semaphore(2)`).
