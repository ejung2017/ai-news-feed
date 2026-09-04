import os
import re
import json
import html
import feedparser
from datetime import datetime, timedelta, timezone
from google import genai
from google.genai import types
from email.mime.text import MIMEText
import base64

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# RSS Feeds to monitor
FEEDS = {
    "OpenAI Blog": "https://openai.com/news/rss.xml",
    "Google AI Blog": "https://blog.google/technology/ai/rss/",
    "Hacker News (AI)": "https://hnrss.org/newest?q=AI"
}

# How far back to collect news. Filtering on "today's date" alone leaves the
# result completely empty in the early UTC hours, when no feed has posted yet.
LOOKBACK_HOURS = 48

GEMINI_MODEL = "gemini-3.6-flash"
# Overly long article summaries only bloat the prompt and hurt summary quality,
# so they get truncated.
MAX_SUMMARY_CHARS = 600


def _strip_html(raw):
    """Strip the tags mixed into an RSS summary and keep only plain text."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_SUMMARY_CHARS]


def fetch_recent_news():
    """Collect articles from the last LOOKBACK_HOURS as (items, failed_feeds)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    items = []
    problems = []
    for feed_name, feed_url in FEEDS.items():
        feed = feedparser.parse(feed_url)

        # Report feeds that could not be fetched at all (404, 502, broken XML,
        # etc.) instead of skipping them silently.
        status = getattr(feed, "status", None)
        if (status is not None and status >= 400) or not feed.entries:
            reason = f"HTTP {status}" if status else (str(getattr(feed, "bozo_exception", "")) or "empty feed")
            problems.append(f"{feed_name} ({reason})")
            continue

        for entry in feed.entries:
            # Some entries have no published_parsed, so fall back to
            # updated_parsed and skip the entry when neither is present.
            parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if not parsed:
                continue
            published = datetime(*parsed[:6], tzinfo=timezone.utc)
            if published >= cutoff:
                items.append({
                    "source": feed_name,
                    "title": entry.title,
                    "link": entry.link,
                    "summary": _strip_html(entry.get("summary", "")),
                })

    return items, problems


def summarize_with_gemini(items):
    """Hand the articles to Gemini and get back (subject, html_body).

    The subject is drawn from the day's most-covered topic rather than a fixed
    phrase.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("The GEMINI_API_KEY environment variable is not set.")

    client = genai.Client(api_key=GEMINI_API_KEY)

    raw_data = "\n---\n".join(
        f"Source: {i['source']}\nTitle: {i['title']}\nLink: {i['link']}\nSummary: {i['summary']}"
        for i in items
    )
    today = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""You are an expert analyst tracking the AI industry.
Below is the raw list of RSS articles collected over the last {LOOKBACK_HOURS} hours.

{raw_data}

Produce the following two things.

1) subject: the subject line for today's email.
   - Scan all the articles and pick the **single most repeated or most consequential topic**, then put that topic in the subject.
   - Generic phrases like "Today's AI News" are forbidden. The subject alone must convey what actually happened that day.
   - Format: "📢 [AI Digest] {today} · <today's key topic>"
   - Write in English, 60 characters or fewer in total.

2) body_html: the email body (HTML).
   - Group the articles by theme (e.g. Models/Research, Tools & Infrastructure, Industry Trends).
   - For each group, lead with a two-sentence summary, then list the items as bullets below it.
   - Each bullet starts with an <a> tag linking the article title to its source URL, followed by a one-sentence analytical comment.
   - Cut the marketing fluff and focus on technical and strategic significance.
   - Write in English, and return only the HTML fragment that goes inside the <body> tag."""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "body_html": {"type": "string"},
                },
                "required": ["subject", "body_html"],
            },
        ),
    )

    data = json.loads(response.text)
    return data["subject"], data["body_html"]


def fallback_content(items):
    """The mail must go out even if the Gemini call fails, so build the raw list without summaries."""
    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"📢 [AI Digest] {today} · AI news briefing (summary failed)"

    by_source = {}
    for i in items:
        by_source.setdefault(i["source"], []).append(i)

    parts = []
    for source, entries in by_source.items():
        links = "".join(f'<li><a href="{i["link"]}">{html.escape(i["title"])}</a></li>' for i in entries)
        parts.append(f"<h3>{html.escape(source)}</h3><ul>{links}</ul>")
    return subject, "".join(parts)


def send_gmail(subject, body_html):
    import smtplib
    from email.mime.multipart import MIMEMultipart

    # Credentials are read only from the environment. Hardcoding them in the
    # source leaks them to the public repository.
    sender_email = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    receiver_email = sender_email

    message = MIMEMultipart()
    message["From"] = sender_email
    message["To"] = receiver_email
    message["Subject"] = subject

    message.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()  # Encrypt the connection before sending credentials.
            server.login(sender_email, app_password)
            server.sendmail(sender_email, receiver_email, message.as_string())

        print("🎉 Email sent successfully! Check your inbox.")

    except Exception as e:
        print(f"An error occurred while sending the email: {e}")


if __name__ == "__main__":
    print("Collecting news...")
    items, problems = fetch_recent_news()
    if problems:
        print("Feeds that failed to fetch:", ", ".join(problems))

    if not items:
        print(f"No new articles in the last {LOOKBACK_HOURS} hours, so no email will be sent.")
    else:
        print(f"Collected {len(items)} articles. Summarizing with Gemini...")
        try:
            subject, body_html = summarize_with_gemini(items)
        except Exception as e:
            print(f"Gemini summary failed ({e}). Falling back to the raw list.")
            subject, body_html = fallback_content(items)

        if problems:
            body_html += "<hr><p style='color:#888;font-size:12px'>Feeds that failed to fetch: " + html.escape(", ".join(problems)) + "</p>"

        print(f"Subject: {subject}")
        send_gmail(subject, body_html)
