import os
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict
import asyncio
import random
import logging

# Set up logging with less verbose output
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Google Search API configuration
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
GOOGLE_SEARCH_ENGINE_ID = os.getenv("GOOGLE_SEARCH_ENGINE_ID")

# Optimized scraping configuration
MAX_RETRIES = 2
MIN_DELAY = 0.2  # Reduced delay
MAX_DELAY = 1.0  # Reduced delay

# Simplified user agents list
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0"
]


def get_request_headers() -> Dict[str, str]:
    """Generate simplified request headers that mimic a browser"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    }


async def google_search(query: str, max_results: int = 5) -> List[str]:
    """Perform a Google search and return a list of URLs"""
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            'key': GOOGLE_SEARCH_API_KEY,
            'cx': GOOGLE_SEARCH_ENGINE_ID,
            'q': query,
            'num': max_results
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            search_results = response.json()

        if 'items' not in search_results:
            return []

        return [item.get('link') for item in search_results['items']]
    except Exception as e:
        logger.error(f"Error in Google search: {str(e)}")
        return []


async def fetch_url_content(url: str, timeout: int = 10) -> str:
    """Fetch content from a URL with minimal retry logic"""
    for attempt in range(MAX_RETRIES):
        try:
            # Only add delay on retry attempts
            if attempt > 0:
                await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

            headers = get_request_headers()

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    timeout=timeout,
                    follow_redirects=True,
                    headers=headers
                )

                if response.status_code == 200:
                    return response.text
                elif response.status_code in (403, 429):
                    # Just retry with different headers for common blocking status codes
                    continue
                else:
                    response.raise_for_status()

        except Exception as e:
            if attempt == MAX_RETRIES - 1:  # Only log on final attempt
                logger.warning(f"Error fetching {url}: {str(e)}")

    return ""


def extract_text_from_html(html: str, max_paragraphs: int = 10) -> str:
    """Extract meaningful text from HTML content - optimized version"""
    if not html:
        return ""

    try:
        soup = BeautifulSoup(html, 'html.parser')

        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.extract()

        # Extract paragraphs and headings - simplified to improve speed
        paragraphs = []
        for tag in soup.find_all(['h1', 'h2', 'p']):  # Reduced tag types
            text = tag.get_text().strip()
            if text and len(text) > 20:  # Only keep substantial content
                paragraphs.append(text)

        return "\n\n".join(paragraphs[:max_paragraphs])
    except Exception as e:
        logger.error(f"Error extracting text: {str(e)}")
        return ""


async def search_and_crawl(query: str, max_urls: int = 3, max_paragraphs: int = 10) -> str:
    """Search, crawl the URLs, and return results - optimized for speed"""
    try:
        # Get URLs from Google
        urls = await google_search(query, max_urls)

        if not urls:
            return f"No search results found for '{query}'"

        # Fetch content from all URLs in parallel
        tasks = [fetch_url_content(url) for url in urls]
        contents = await asyncio.gather(*tasks)

        # Process results
        results = []
        for url, html_content in zip(urls, contents):
            if html_content:
                text_content = extract_text_from_html(html_content, max_paragraphs)
                if text_content:
                    results.append(f"SOURCE: {url}\n\n{text_content}")

        if not results:
            return f"No usable content found for '{query}'"

        # Combine results
        return "\n\n---\n\n".join(results)

    except Exception as e:
        logger.error(f"Error processing search: {str(e)}")
        return f"Error processing search: {str(e)}"