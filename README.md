# AI News Feed

A Python project for aggregating and surfacing AI news.

## Setup

Requires Python 3.10 or newer.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Gmail authentication

`credentials.json` must be an OAuth 2.0 client file downloaded from Google Cloud. Do not create it by hand or commit it.

1. Open the [Google Cloud Console](https://console.cloud.google.com/), create or select a project, and enable the **Gmail API**.
2. Configure the OAuth consent screen. For an **External** app in testing, add the Gmail account that will send the briefing as a test user.
3. Go to **APIs & Services > Credentials > Create credentials > OAuth client ID**, choose **Desktop app**, and download the JSON file.
4. Rename the downloaded file to `credentials.json` and place it in this repository's top-level directory, beside `pyproject.toml`.

The first run opens a browser for Google authorization. Approve the requested Gmail sending permission; the flow saves `token.json` locally for later runs.

If Google reports `Error 403: org_internal`, return to **APIs & Services > OAuth consent screen** and set the app's user type to **External**. If Google does not allow changing the user type, create a new Google Cloud project and OAuth client with **External** selected. For an External app in testing, add the sending Gmail account under **Test users**. Delete `token.json` and retry after changing the OAuth configuration.

```bash
export GEMINI_API_KEY="your-gemini-api-key"
python -m ai_news_feed.main
```

If you need to authorize a different Google account, delete the local `token.json` and run the command again. Both `credentials.json` and `token.json` are excluded by `.gitignore`.

## Development

```bash
pytest
```
