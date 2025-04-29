import os
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict, Any
import asyncio
import random
import logging

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
GOOGLE_SEARCH_ENGINE_ID = os.getenv("GOOGLE_SEARCH_ENGINE_ID")

MAX_RETRIES = 2
MIN_DELAY = 0.2
MAX_DELAY = 1.0
DEFAULT_MAX_URLS = 3
DEFAULT_MAX_PARAGRAPHS = 10
FETCH_TIMEOUT = 10

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

async def google_search(query: str, max_results: int) -> List[str]:
    """Perform a Google search and return a list of URLs"""
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
         logger.error("Google Search API Key or Engine ID is missing.")
         return ["Error: Google Search API credentials are not configured."]
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

        return [item['link'] for item in search_results['items'] if 'link' in item]

    except httpx.HTTPStatusError as e:
         logger.error(f"HTTP error during Google search for '{query}': {e.response.status_code} - {e.response.text}")
         return [f"Error: Google Search failed with status {e.response.status_code}."]
    except Exception as e:
        logger.error(f"Error in Google search for '{query}': {str(e)}")
        return [f"Error: An unexpected error occurred during Google Search: {str(e)}"]


async def fetch_url_content(url: str) -> str:
    """Fetch content from a URL with minimal retry logic"""
    for attempt in range(MAX_RETRIES):
        try:
            if attempt > 0:
                await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

            headers = get_request_headers()

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    timeout=FETCH_TIMEOUT,
                    follow_redirects=True,
                    headers=headers
                )

                # Check for common blocking status codes first
                if response.status_code in (403, 429):
                    logger.warning(f"Blocked ({response.status_code}) fetching {url} on attempt {attempt+1}.")
                    continue # Retry potentially with different headers/timing

                # Raise errors for other bad statuses (4xx, 5xx)
                response.raise_for_status()

                # Check content type - avoid parsing non-html
                content_type = response.headers.get('content-type', '').lower()
                if 'html' not in content_type:
                     logger.warning(f"Skipping non-HTML content ({content_type}) from {url}")
                     return ""

                return response.text

        except httpx.TimeoutException:
             logger.warning(f"Timeout fetching {url} on attempt {attempt+1}.")

        except httpx.RequestError as e:
            # Includes connection errors, redirect loops, etc. Only log on final attempt.
            if attempt == MAX_RETRIES - 1:
                logger.warning(f"Request error fetching {url} after {MAX_RETRIES} attempts: {str(e)}")

        except Exception as e:
            # Catch other unexpected errors (like invalid URL, SSL issues)
            if attempt == MAX_RETRIES - 1:
                logger.warning(f"Unexpected error fetching {url}: {str(e)}")
            break
    return ""


def extract_text_from_html(html: str, max_paragraphs: int) -> str:
    """Extract meaningful text from HTML content - optimized version"""
    if not html:
        return ""

    try:
        soup = BeautifulSoup(html, 'html.parser')

        for element in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            element.extract()

        main_content = soup.find('main') or \
                       soup.find('article') or \
                       soup.find(id='content') or \
                       soup.find(class_='content') or \
                       soup.find(id='main') or \
                       soup.find(class_='main')

        target_element = main_content if main_content else soup
        paragraphs = []

        for tag in target_element.find_all(['p', 'h1', 'h2', 'h3', 'li']):
            text = tag.get_text(' ', True)

            if text and 20 < len(text) < 5000 and 'function(' not in text and '{' not in text:
                paragraphs.append(text)
            if len(paragraphs) >= max_paragraphs * 1.5:
                break

        seen_starts = set()
        unique_paragraphs = []
        for p in paragraphs:
             start_phrase = " ".join(p.split()[:5])
             if start_phrase not in seen_starts:
                 unique_paragraphs.append(p)
                 seen_starts.add(start_phrase)


        return "\n\n".join(unique_paragraphs[:max_paragraphs])

    except Exception as e:
        logger.error(f"Error extracting text: {str(e)}")
        return ""


async def search_and_crawl(args: Dict[str, Any]) -> str:
    """
    Performs a web search using the Google Search API, crawls the top results,
    extracts text content, and returns a formatted summary.

    Args:
        args (Dict[str, Any]): A dictionary containing the arguments.
            Expected key: 'query' (str) - The search query.
            Optional keys: 'max_urls' (int), 'max_paragraphs' (int)

    Returns:
        str: A formatted string containing the search results, or an error message.
    """
    query = args.get("query")
    if not query or not isinstance(query, str):
        return "Error: Missing or invalid 'query' argument for web search."

    # Get optional parameters or use defaults
    try:
        max_urls = int(args.get("max_urls", DEFAULT_MAX_URLS))
        max_paragraphs = int(args.get("max_paragraphs", DEFAULT_MAX_PARAGRAPHS))
        # Add some sanity limits
        max_urls = max(1, min(max_urls, 5)) # Limit to 1-5 URLs
        max_paragraphs = max(1, min(max_paragraphs, 20)) # Limit to 1-20 paragraphs per URL
    except (ValueError, TypeError):
         return f"Error: Invalid 'max_urls' or 'max_paragraphs' argument. They must be integers."

    try:
        logger.info(f"Performing web search for: '{query}' (Max URLs: {max_urls})")
        # Get URLs from Google
        urls_or_error = await google_search(query, max_urls)

        # Handle potential error message returned from google_search
        if isinstance(urls_or_error, list) and len(urls_or_error) == 1 and urls_or_error[0].startswith("Error:"):
            return urls_or_error[0]

        urls = urls_or_error
        if not urls:
            return f"No search results found for '{query}' via Google Search API."

        logger.info(f"Found {len(urls)} URLs. Fetching content...")
        tasks = [fetch_url_content(url) for url in urls]
        contents = await asyncio.gather(*tasks)

        results = []
        for url, html_content in zip(urls, contents):
            if html_content:
                logger.debug(f"Extracting text from {url}...")
                text_content = extract_text_from_html(html_content, max_paragraphs)
                if text_content:
                    results.append(f"SOURCE: {url}\n\n{text_content.strip()}")
                else:
                     logger.warning(f"Could not extract meaningful text from {url}")
        if not results:
            return f"Found search results for '{query}', but could not extract usable content from the pages."

        logger.info(f"Successfully extracted content from {len(results)} sources.")
        return "\n\n---\n\n".join(results)

    except Exception as e:
        logger.error(f"Unexpected error processing search for '{query}': {str(e)}", exc_info=True)
        return f"Error: An unexpected error occurred while processing the search: {str(e)}"
