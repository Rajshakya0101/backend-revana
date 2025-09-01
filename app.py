from flask import Flask, jsonify, request
from flask_cors import CORS
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
import requests, logging, os, emoji, time

# ──────────────────────────────────────────────────────────────────────────────
# Flask + Logging
# ──────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("flipkart-scraper")

BASE_URL = "https://www.flipkart.com"
SESSION_STATE = "flipkart_state.json"  # persistent browser storage (cookies/localStorage)

# A standard header set you can reuse for any residual requests fallbacks
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ──────────────────────────────────────────────────────────────────────────────
# Playwright helpers
# ──────────────────────────────────────────────────────────────────────────────
def get_html_with_playwright(url: str) -> str:
    """
    Load a URL in a real Chromium browser via Playwright and return page HTML.
    Uses a persistent storage state to reduce bot checks.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)  # If blocked, try headful=False
        context = p.chromium.launch_persistent_context(
            user_data_dir=".flipkart_profile",
            headless=True,
            locale="en-IN",
            viewport={"width": 1366, "height": 768},
            args=[
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
            ]
        )

        try:
            page = context.new_page()
            # Extra headers for the page
            page.set_extra_http_headers({
                "Accept-Language": "en-IN,en;q=0.9",
            })

            # Go to URL
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Try closing any login/consent modal(s)
            _dismiss_popups(page)

            # Wait for something meaningful on product pages
            # Product name often present; adjust if Flipkart changes selectors
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeoutError:
                pass

            html = page.content()
            return html
        finally:
            try:
                context.storage_state(path=SESSION_STATE)
            except Exception:
                pass
            context.close()
            browser.close()


def _dismiss_popups(page):
    """
    Try to close common Flipkart modals/popups (login/consent).
    These selectors change; we try a few harmless attempts.
    """
    candidates = [
        "button:has-text('✕')",
        "button:has-text('Close')",
        "text=✕",
        "div._2KpZ6l._2doB4z",   # legacy close button class
    ]
    for sel in candidates:
        try:
            page.click(sel, timeout=2000)
            time.sleep(0.3)
        except Exception:
            pass


def collect_review_pages(url: str, max_pages: int = 5) -> list[str]:
    """
    Starting from the first Reviews page, collect up to `max_pages` review pages’ HTML.
    We navigate with Playwright and click “Next” if available.
    """
    pages_html = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = p.chromium.launch_persistent_context(
            user_data_dir=".flipkart_profile",
            headless=True,
            locale="en-IN",
            viewport={"width": 1366, "height": 768},
        )
        try:
            page = context.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-IN,en;q=0.9"})
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            _dismiss_popups(page)

            for _ in range(max_pages):
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except PWTimeoutError:
                    pass

                pages_html.append(page.content())

                # Try to click “Next” in pagination. Flipkart varies selectors; try a few.
                next_clicked = False
                for sel in [
                    "a[rel='next']",
                    "a._9QVEpD:has-text('Next')",  # new class example
                    "a:has-text('Next')",
                    "nav a:has-text('Next')"
                ]:
                    try:
                        page.click(sel, timeout=2000)
                        next_clicked = True
                        time.sleep(0.8)
                        _dismiss_popups(page)
                        break
                    except Exception:
                        continue

                if not next_clicked:
                    break  # no more pages
            return pages_html
        finally:
            try:
                context.storage_state(path=SESSION_STATE)
            except Exception:
                pass
            context.close()
            browser.close()


# ──────────────────────────────────────────────────────────────────────────────
# Parsing + sentiment
# ──────────────────────────────────────────────────────────────────────────────
class ReviewScraper:
    def __init__(self):
        self.reviews = []
        self.review_titles = []
        self.ratings = []
        self.sentiments = []
        self.product_details = {}
        self.analyzer = SentimentIntensityAnalyzer()

    def extract_product_details(self, soup: BeautifulSoup):
        product_name = soup.find('span', class_="VU-ZEz")
        product_price = soup.find('div', class_="Nx9bqj CxhGGd")

        fields, values = [], []
        try:
            if soup.find('div', "col col-3-12 _9NUIO9"):
                fields = [x.text.strip() for x in soup.find_all('div', "col col-3-12 _9NUIO9")]
            elif soup.find('td', class_="+fFi1w col col-3-12"):
                fields = [x.text.strip() for x in soup.find_all('td', class_="+fFi1w col col-3-12")]
        except Exception:
            pass

        try:
            if soup.find('li', class_="HPETK2"):
                values = [x.text.strip() for x in soup.find_all('li', class_="HPETK2")]
            elif soup.find('div', "col col-9-12 -gXFvC"):
                values = [x.text.strip() for x in soup.find_all('div', "col col-9-12 -gXFvC")]
        except Exception:
            pass

        image_tag = None
        try:
            if soup.find('img', class_="DByuf4 IZexXJ jLEJ7H"):
                image_tag = soup.find('img', class_="DByuf4 IZexXJ jLEJ7H")
            elif soup.find('img', class_="_53J4C- utBuJY"):
                image_tag = soup.find('img', class_="_53J4C- utBuJY")
        except Exception:
            pass

        image_url = image_tag.get("src") if image_tag else "Image not found"

        spec_map = {}
        for i in range(min(len(fields), len(values))):
            spec_map[fields[i]] = values[i]

        self.product_details = {
            "Product Name": product_name.text.strip() if product_name else "Name not found",
            "Product Price": product_price.text.strip() if product_price else "Price not found",
            "Image URL": image_url,
            "Specifications": spec_map
        }

    def extract_review_data_from_soup(self, soup: BeautifulSoup):
        # Review text blocks
        self.reviews += [
            r.text.replace('READ MORE', '').strip()
            for r in soup.find_all('div', class_='ZmyHeo')
        ]
        # Titles
        self.review_titles += [t.text.strip() for t in soup.find_all('p', class_='z9E0IG')]
        # Ratings (Flipkart sometimes wraps rating number in these classes)
        if soup.find('div', class_="XQDdHH Ga3i8K _9lBNRY"):
            self.ratings += [r.text.strip() for r in soup.find_all('div', class_="XQDdHH Ga3i8K _9lBNRY")]
        elif soup.find('div', class_="XQDdHH Ga3i8K"):
            self.ratings += [r.text.strip() for r in soup.find_all('div', class_="XQDdHH Ga3i8K")]

    def analyze_sentiment(self):
        for review in self.reviews:
            cleaned = emoji.demojize(review)
            score = self.analyzer.polarity_scores(cleaned)
            c = score["compound"]
            if c >= 0.05:
                self.sentiments.append("positive")
            elif c <= -0.05:
                self.sentiments.append("negative")
            else:
                self.sentiments.append("neutral")

    def get_sentiment_distribution(self):
        out = {"positive": 0, "neutral": 0, "negative": 0}
        for s in self.sentiments:
            out[s] += 1
        return out

    def get_rating_distribution(self):
        out = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
        for r in self.ratings:
            if r in out:
                out[r] += 1
        return out

    def generate_wordcloud_text(self):
        text = " ".join(self.reviews)
        return emoji.replace_emoji(text, "")


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/scrape_reviews", methods=["POST"])
def scrape_reviews():
    try:
        body = request.get_json(silent=True) or {}
        product_url = body.get("url", "").strip()
        if not product_url:
            return jsonify({"ok": False, "error": "URL is required"}), 400

        # 1) Load product page with Playwright
        try:
            product_html = get_html_with_playwright(product_url)
        except Exception as e:
            log.warning("Playwright failed on product page: %s", e)
            # As a mild fallback, try requests (may still 403)
            try:
                r = requests.get(product_url, headers=REQUEST_HEADERS, timeout=20)
                r.raise_for_status()
                product_html = r.text
            except requests.RequestException as re:
                log.error("Failed to load product page: %s", re)
                return jsonify({"ok": False, "error": "Blocked or unreachable product page"}), 502

        product_soup = BeautifulSoup(product_html, "html.parser")

        scraper = ReviewScraper()
        scraper.extract_product_details(product_soup)

        # 2) Find the "All reviews" link on product page
        review_page_url = _find_first_review_page_url(product_soup)
        if not review_page_url:
            return jsonify({"ok": False, "error": "No review page found"}), 404

        # 3) Collect multiple review pages (Playwright pagination)
        pages_html = collect_review_pages(review_page_url, max_pages=5)

        # 4) Parse reviews from each page
        for html in pages_html:
            page_soup = BeautifulSoup(html, "html.parser")
            scraper.extract_review_data_from_soup(page_soup)

        # 5) Sentiment + outputs
        scraper.analyze_sentiment()
        wordcloud_text = scraper.generate_wordcloud_text()

        return jsonify({
            "ok": True,
            "message": "Scraping completed successfully!",
            "product_details": scraper.product_details,
            "reviews_scraped": len(scraper.reviews),
            "rating_distribution": scraper.get_rating_distribution(),
            "sentiment_distribution": scraper.get_sentiment_distribution(),
            "wordcloud": wordcloud_text,     # raw text for your front-end wordcloud
            "sample_reviews": scraper.reviews[:5],
            "sample_titles": scraper.review_titles[:5],
        }), 200

    except Exception as e:
        log.exception("Unhandled server error")
        return jsonify({"ok": False, "error": str(e)}), 500


def _find_first_review_page_url(soup: BeautifulSoup) -> str | None:
    """
    Attempt to find the "All reviews" link from the product page.
    Your original heuristic was 'anchor whose text contains "All"'; we’ll keep it,
    but also try other common patterns.
    """
    # Try explicit “View All”/“All Reviews” anchors
    for a in soup.find_all("a", href=True):
        txt = (a.get_text() or "").strip().lower()
        if "all" in txt and "review" in txt:
            return BASE_URL + a["href"]

    # Fallback: any anchor whose href contains "/product-reviews/"
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/product-reviews/" in href:
            return BASE_URL + href

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
