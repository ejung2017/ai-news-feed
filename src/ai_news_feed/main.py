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

# 며칠 치 뉴스를 모을지. "오늘 날짜"로만 거르면 UTC 기준 새벽에는
# 어느 피드에도 오늘 자 글이 없어서 결과가 통째로 비어버립니다.
LOOKBACK_HOURS = 48

GEMINI_MODEL = "gemini-3.6-flash"
# 기사 본문 요약이 너무 길면 프롬프트만 커지고 요약 품질은 안 좋아져서 잘라 씁니다.
MAX_SUMMARY_CHARS = 600


def _strip_html(raw):
    """RSS summary 안에 섞여 있는 태그를 걷어내고 순수 텍스트만 남깁니다."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_SUMMARY_CHARS]


def fetch_recent_news():
    """최근 LOOKBACK_HOURS 시간 내 기사를 모아 (기사목록, 실패한피드목록)으로 돌려줍니다."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    items = []
    problems = []
    for feed_name, feed_url in FEEDS.items():
        feed = feedparser.parse(feed_url)

        # 피드 자체를 못 가져온 경우(404, 502, XML 깨짐 등)를 조용히 넘기지 않고 알려줍니다.
        status = getattr(feed, "status", None)
        if (status is not None and status >= 400) or not feed.entries:
            reason = f"HTTP {status}" if status else (str(getattr(feed, "bozo_exception", "")) or "빈 피드")
            problems.append(f"{feed_name} ({reason})")
            continue

        for entry in feed.entries:
            # published_parsed가 없는 항목도 있어서 updated_parsed로 대체하고, 둘 다 없으면 건너뜁니다.
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
    """Gemini에게 기사들을 넘겨 (제목, HTML본문)을 받아옵니다.

    제목은 고정 문구가 아니라 그날 가장 많이 다뤄진 주제에서 뽑아냅니다.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 환경 변수가 설정되어 있지 않습니다.")

    client = genai.Client(api_key=GEMINI_API_KEY)

    raw_data = "\n---\n".join(
        f"Source: {i['source']}\nTitle: {i['title']}\nLink: {i['link']}\nSummary: {i['summary']}"
        for i in items
    )
    today = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""당신은 AI 업계를 추적하는 전문 애널리스트입니다.
아래는 최근 {LOOKBACK_HOURS}시간 동안 수집된 RSS 기사 원문 목록입니다.

{raw_data}

다음 두 가지를 생성하세요.

1) subject: 오늘 메일의 제목.
   - 기사 전체를 훑어 **가장 많이 반복되거나 가장 파급력이 큰 단 하나의 주제**를 골라 그 주제를 제목에 담으세요.
   - "오늘의 AI 뉴스" 같은 뻔한 문구는 금지. 그날 실제로 무슨 일이 있었는지 제목만 봐도 알 수 있어야 합니다.
   - 형식: "📢 [AI 비서] {today} · <오늘의 핵심 주제>"
   - 한국어로, 전체 60자 이내.

2) body_html: 메일 본문(HTML).
   - 기사를 주제별로 묶으세요 (예: 모델/연구, 도구·인프라, 산업 동향).
   - 각 그룹마다 2문장짜리 핵심 요약을 먼저 쓰고, 그 아래 항목을 불릿으로 나열하세요.
   - 각 불릿은 기사 제목을 원문 URL로 링크한 <a> 태그로 시작하고, 이어서 한 문장짜리 분석 코멘트를 답니다.
   - 마케팅성 미사여구는 걷어내고 기술적·전략적 의미에 집중하세요.
   - 한국어로 작성하고, <body> 태그 안에 들어갈 HTML 조각만 반환하세요."""

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
    """Gemini 호출이 실패해도 메일은 나가야 하니, 요약 없이 원본 목록만이라도 만듭니다."""
    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"📢 [AI 비서] {today} · AI 뉴스 브리핑 (요약 실패)"

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

    # 자격 증명은 환경 변수에서만 읽습니다. 소스에 하드코딩하면 공개 저장소로 새어 나갑니다.
    sender_email = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    receiver_email = sender_email

    # 2. 편지봉투(MIMEMultipart)를 만들고 주소와 제목을 적습니다.
    message = MIMEMultipart()
    message["From"] = sender_email
    message["To"] = receiver_email
    message["Subject"] = subject

    # 편지지에 글을 적어(MIMEText) 편지봉투에 쏙 집어넣습니다.
    message.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        # 4. 구글 우체국의 문을 열고(주소: smtp.gmail.com, 포트번호: 587) 연결합니다.
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls() # 편지 내용을 스파이가 훔쳐보지 못하게 '암호화' 가방에 담는 과정입니다.

            # 5. 내 아이디와 16자리 비밀 열쇠로 우체국 직원에게 로그인 인증을 받습니다.
            server.login(sender_email, app_password)

            # 6. 준비된 편지봉투를 우체통에 쏙 집어넣어 발송합니다!
            server.sendmail(sender_email, receiver_email, message.as_string())

        print("🎉 이메일이 성공적으로 발송되었습니다! 스마트폰 메일함을 확인해 보세요!")

    except Exception as e:
        print(f"메일 발송 중 오류가 발생했습니다: {e}")


if __name__ == "__main__":
    print("뉴스 수집 중...")
    items, problems = fetch_recent_news()
    if problems:
        print("수집 실패한 피드:", ", ".join(problems))

    if not items:
        print(f"최근 {LOOKBACK_HOURS}시간 내 새로운 뉴스가 없어 메일을 보내지 않습니다.")
    else:
        print(f"기사 {len(items)}건 수집. Gemini로 요약 중...")
        try:
            subject, body_html = summarize_with_gemini(items)
        except Exception as e:
            print(f"Gemini 요약 실패({e}). 원본 목록으로 대체합니다.")
            subject, body_html = fallback_content(items)

        if problems:
            body_html += "<hr><p style='color:#888;font-size:12px'>수집 실패한 피드: " + html.escape(", ".join(problems)) + "</p>"

        print(f"제목: {subject}")
        send_gmail(subject, body_html)
