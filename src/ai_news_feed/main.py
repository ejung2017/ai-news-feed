# Setup requirements:
# pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client feedparser google-genai

import os
import feedparser
from datetime import datetime, timedelta, timezone
from google import genai
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from email.mime.text import MIMEText
import base64

# Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "your_gemini_api_key_here")
GEMINI_MODEL = "gemini-2.5-flash"
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# RSS Feeds to monitor
FEEDS = {
    "Anthropic Blog": "https://www.anthropic.com/news.rss",
    "OpenAI Blog": "https://openai.com/news/rss.xml",
    "Google AI Blog": "https://blog.google/technology/ai/rss/",
    "Hacker News (AI)": "https://hnrss.org/search?q=AI"
}

def get_gmail_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

def fetch_recent_news():
    news_items = []
    now = datetime.now(timezone.utc)
    one_day_ago = now - timedelta(days=1)

    for source, url in FEEDS.items():
        feed = feedparser.parse(url)
        for entry in feed.entries:
            # Fallback parsing for published dates
            published_time = None
            for date_field in ['published_parsed', 'updated_parsed']:
                if hasattr(entry, date_field) and getattr(entry, date_field):
                    published_time = datetime(*getattr(entry, date_field)[:6], tzinfo=timezone.utc)
                    break

            if published_time and published_time > one_day_ago:
                news_items.append({
                    "source": source,
                    "title": entry.title,
                    "link": entry.link,
                    "summary": entry.get("summary", "No summary available.")
                })
    return news_items

def synthesize_with_gemini(news_items):
    if not news_items:
        return "<p>No new AI updates found in the past 24 hours.</p>"

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Format raw data for prompt injection
    raw_data = ""
    for item in news_items:
        raw_data += f"Source: {item['source']}\nTitle: {item['title']}\nLink: {item['link']}\nSummary: {item['summary']}\n---\n"

    prompt = f"""You are an expert AI researcher. Analyze and curate the following raw RSS news entries from the last 24 hours:

    {raw_data}

    Generate a clean, high-density HTML email briefing. Group the items logically (e.g., Core Models, Tools/Infrastructure, Industry Shifts). For each group, write a 2-sentence executive summary, followed by bulleted items. Each bullet must feature the exact title linked to its source URL, followed by a concise 1-sentence analytical takeaway. Cut all marketing fluff and focus strictly on engineering or architectural significance. Return ONLY the HTML code wrapped inside <body> tags."""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    return response.text

def send_email(service, html_content):
    user_profile = service.users().getProfile(userId='me').execute()
    my_email = user_profile['emailAddress']

    message = MIMEText(html_content, 'html')
    message['to'] = my_email
    message['from'] = my_email
    message['subject'] = f"AI News Briefing - {datetime.now().strftime('%Y-%m-%d')}"

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(userId='me', body={'raw': raw}).execute()
    print("Email sent successfully!")

if __name__ == "__main__":
    print("Fetching news...")
    raw_news = fetch_recent_news()
    print(f"Found {len(raw_news)} recent articles. Synthesizing with Gemini...")
    digest_html = synthesize_with_gemini(raw_news)
    print("Connecting to Gmail API...")
    gmail_client = get_gmail_service()
    send_email(gmail_client, digest_html)
