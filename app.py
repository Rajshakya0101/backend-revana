from flask import Flask, jsonify, request
import requests
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from flask_cors import CORS
import emoji
import logging
import os
# from wordcloud import WordCloud
# import base64
# from io import BytesIO

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 Edg/91.0.864.59",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

base_url = "https://www.flipkart.com"


def make_soup(product_url):
    """Fetch the HTML content of a URL and return a BeautifulSoup object."""
    try:
        response = requests.get(product_url, headers=headers)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching URL: {e}")
        return None


def get_first_review_page_url(soup):
    """Find and return the URL of the first review page."""
    try:
        for a_tag in soup.find_all('a', href=True):
            if 'All' in a_tag.get_text():  # Check for the text inside the <a> tag
                href_value = a_tag['href']
                return base_url + href_value
        return None
    except Exception as e:
        logging.error(f"Error extracting review page URL: {e}")
        return None


class ReviewScraper:
    def __init__(self, product_url):
        self.product_url = product_url
        self.reviews = []
        self.review_titles = []
        self.ratings = []
        self.sentiments = []
        self.page_urls = []
        self.product_details = {}
        self.analyzer = SentimentIntensityAnalyzer()

    def extract_product_details(self, soup):
        """Extract product details such as name, price, specifications, and image URL."""
        product_name = soup.find('span', class_="VU-ZEz")
        product_price = soup.find('div', class_="Nx9bqj CxhGGd")
        fields=[]
        values=[]
        if soup.find('div',"col col-3-12 _9NUIO9"):
          fields = [x.text.strip() for x in soup.find_all('div',"col col-3-12 _9NUIO9")]
        elif soup.find('td', class_="+fFi1w col col-3-12"):
          fields = [x.text.strip() for x in soup.find_all('td', class_="+fFi1w col col-3-12")]
        if soup.find('li', class_="HPETK2"):
          values = [x.text.strip() for x in soup.find_all('li', class_="HPETK2")]
        elif soup.find('div',"col col-9-12 -gXFvC"):
          values = [x.text.strip() for x in soup.find_all('div',"col col-9-12 -gXFvC")]    
        image_tag=None
        if soup.find('img', class_="DByuf4 IZexXJ jLEJ7H"):
          image_tag = soup.find('img', class_="DByuf4 IZexXJ jLEJ7H")
        elif soup.find('img', class_="_53J4C- utBuJY"):
          image_tag = soup.find('img', class_="_53J4C- utBuJY")
        image_url = image_tag.get("src") if image_tag else "Image not found"
     

        self.product_details = {
            "Product Name": product_name.text.strip() if product_name else "Name not found",
            "Product Price": product_price.text.strip() if product_price else "Price not found",
            "Image URL": image_url,
            "Specifications": {fields[i]: values[i] for i in range(len(fields))}
        }

    def extract_review_data(self, soup):
        """Extract reviews, titles, and ratings from a reviews page."""
        self.reviews += [r.text.replace('READ MORE', '').strip() for r in soup.find_all('div', class_='ZmyHeo')]
        self.review_titles += [t.text.strip() for t in soup.find_all('p', class_='z9E0IG')]
        if soup.find('div',class_="XQDdHH Ga3i8K _9lBNRY"):
          self.ratings += [r.text.strip() for r in soup.find_all('div', class_="XQDdHH Ga3i8K _9lBNRY")]
        elif soup.find('div', class_='XQDdHH Ga3i8K'):
          self.ratings += [r.text.strip() for r in soup.find_all('div', class_="XQDdHH Ga3i8K")]
             

    def extract_pagination_urls(self, soup):
        """Extract all review page URLs from the pagination section."""
        self.page_urls += [base_url + link['href'] for link in soup.find_all('a', class_='cn++Ap')]

    def fetch_reviews(self, review_page_url):
        """Fetch reviews starting from the review-specific page."""
        soup = make_soup(review_page_url)
        if not soup:
            return

        # Extract pagination URLs
        self.extract_pagination_urls(soup)

        # Scrape reviews from all review pages
        for url in self.page_urls:
            page_soup = make_soup(url)
            if page_soup:
                self.extract_review_data(page_soup)

    def analyze_sentiment(self):
        """Analyze sentiment for each review."""
        for review in self.reviews:
            cleaned_review = emoji.demojize(review)
            score = self.analyzer.polarity_scores(cleaned_review)
            compound_score = score['compound']
            if compound_score >= 0.05:
                sentiment = "positive"
            elif compound_score <= -0.05:
                sentiment = "negative"
            else:
                sentiment = "neutral"
            self.sentiments.append(sentiment)

    def get_sentiment_distribution(self):
        """Get sentiment distribution."""
        sentiment_counts = {"positive": 0, "neutral": 0, "negative": 0}
        for sentiment in self.sentiments:
            sentiment_counts[sentiment] += 1
        return sentiment_counts

    def get_rating_distribution(self):
        """Get rating distribution."""
        rating_counts = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
        for rating in self.ratings:
            if rating in rating_counts:
                rating_counts[rating] += 1
        return rating_counts

    def generate_wordcloud(self):
        """Generate data for a word cloud."""
        text = " ".join(self.reviews)
        # Remove emojis for cleaner word cloud text
        text = emoji.replace_emoji(text, "")
        return text


@app.route("/scrape_reviews", methods=["POST"])
def scrape_reviews():
    """API endpoint for scraping product details and reviews."""
    content = request.get_json()
    product_url = content.get('url')

    if not product_url:
        return jsonify({"error": "URL is required"}), 400

    soup = make_soup(product_url)
    if not soup:
        return jsonify({"error": "Failed to fetch product page"}), 500

    scraper = ReviewScraper(product_url)

    # Extract product details
    scraper.extract_product_details(soup)

    # Get the first review page URL
    review_page_url = get_first_review_page_url(soup)
    if not review_page_url:
        return jsonify({"error": "No review page found"}), 404

    # Scrape reviews from the review page
    scraper.fetch_reviews(review_page_url)
    scraper.analyze_sentiment()

    # Generate word cloud
    wordcloud_base64 = scraper.generate_wordcloud()

    return jsonify({
        "message": "Scraping completed successfully!",
        "product_details": scraper.product_details,
        "reviews_scraped": len(scraper.reviews),
        "rating_distribution": scraper.get_rating_distribution(),
        "sentiment_distribution": scraper.get_sentiment_distribution(),
        "wordcloud": wordcloud_base64,
        "sample_reviews": scraper.reviews[:5]
    })


if __name__ == "__main__":
    # Use Render's PORT environment variable if available
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)