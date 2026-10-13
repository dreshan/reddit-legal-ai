# Reddit Legal Content AI – Local Prototype

A local proof of concept that:

1. reads recent posts from configured subreddits via the Reddit Data API,
2. runs a cheap keyword pre-filter (`FILTERED_OUT` / `CANDIDATE_FOR_AI`),
3. sends candidates to Claude for structured legal-relevance analysis,
4. applies a deterministic human-review policy,
5. prints the result and saves it as JSON under `data/results/`.

> **All AI output is labelled "AI-generated preliminary analysis – human review required."**
> It is not legal advice and must be reviewed by a qualified human.

No Docker, no cloud, no database. Runs on Windows with Python 3.11+.

---

## 1. Reddit authentication – read this first

| Question | Answer |
|---|---|
| **Auth approach** | **Application-only OAuth** (`client_credentials` grant), used through PRAW in *read-only* mode. |
| **Why it fits** | The prototype only *reads* public subreddits. It doesn't post, vote or act as a user, so it needs no user login. It's the simplest OAuth flow Reddit supports, and it still gets the authenticated rate limit. |
| **Redirect URL needed?** | **No.** Reddit's app form makes you enter one, but this flow never uses it. Enter `http://localhost:8080`. |
| **Username/password needed?** | **No.** `REDDIT_USERNAME` / `REDDIT_PASSWORD` are optional and blank by default. If you set **both**, the client switches to the script-app *password grant* and acts as that user. This prototype doesn't need that. |
| **App type to register** | **"script"** (personal-use script). It gets a client ID and a client secret, which is what `client_credentials` requires. |
| **Mandatory credentials** | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` |

### Current Reddit API limitations (verify before you start)

Reddit's API access rules changed significantly in 2025–2026. Reddit's own policy pages couldn't be fetched automatically when this was written, so the points below come from third-party summaries (listed under Sources). **Confirm them against Reddit's official Responsible Builder Policy, Developer Terms and Data API Terms.**

* **Approval comes first.** Since November 2025, under Reddit's *Responsible Builder Policy*, new Data API access, including new apps and OAuth tokens, needs Reddit's manual approval. Before that, you could create an app at `reddit.com/prefs/apps` and use it straight away. Now you may have to submit an access request explaining your use case and wait. Reports say this takes weeks and approvals are rare. **This is the biggest risk to the project.** Request access early.
* **Commercial use needs explicit written approval.** A legal organisation analysing Reddit content for business purposes is very likely *commercial use*. Clear this with Reddit, and with your own legal and compliance team, before going past a local test.
* **AI use of Reddit data.** Reddit's Data API Terms restrict how Reddit content can be used with AI and ML, for example model training. Sending posts to a third-party LLM for analysis has to be checked against the current terms and covered in your approval request.
* **Deleted content.** The Data API Terms expect deleted Reddit content to be removed from your systems. The JSON files in `data/results/` hold post text. Treat them as temporary, and plan a deletion or sync process before any production use.
* **Rate limits.** OAuth clients have a per-client-ID rate limit (historically about 100 queries per minute on the free tier). Unauthenticated `.json` access is blocked. PRAW handles rate-limit headers automatically. This app maps HTTP 429 to `RedditRateLimitError` and stops collecting more subreddits.
* **User-Agent.** Reddit requires a unique, descriptive user agent: `<platform>:<app id>:<version> (by /u/<username>)`. Generic agents get throttled or blocked.
* **Developer Platform (Devvit).** Reddit is steering new integrations towards Devvit, its hosted app platform, which does not run Python. This prototype uses the Data API through PRAW, which still works with approved credentials. Keep an eye on Reddit's migration announcements.

Sources (third-party summaries; verify against Reddit's official pages):
[redditapis.com – Data API in 2026](https://www.redditapis.com/blogs/reddit-data-api-2026) ·
[redditapis.com – Commercial use](https://www.redditapis.com/blogs/reddit-api-commercial-use-licensing-explained-2026) ·
[redditapis.com – Developer Platform migration](https://www.redditapis.com/blogs/reddit-developer-platform-migration-2026) ·
[prowlo.com – Data API terms](https://prowlo.com/blog/reddit-data-api) ·
[keyapi.ai – Data API overhaul](https://www.keyapi.ai/blog/reddit-data-api-overhaul-major-changes-and-migration-guide/) ·
[fetchlayer.dev – What still works](https://fetchlayer.dev/blog/reddit-api-closed-2026)

**You can build and test everything without Reddit access.** The unit tests use fictional sample data and mocks, and `--dry-run` skips Claude.

---

## 2. Prerequisites

* Python **3.11+** (tested with 3.13)
* Reddit API access approved, plus a **script** app (client ID and secret)
* An **Anthropic API key** from https://console.anthropic.com/

## 3. Installation (Windows)

```bash
cd reddit-legal-ai
```

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

(In Git Bash, activate with `source .venv/Scripts/activate`.)

## 4. Configuration

```bash
copy .env.example .env
```

Edit `.env` and fill in at least:

```
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=windows:reddit-legal-ai-prototype:v0.1.0 (by /u/your_username)
ANTHROPIC_API_KEY=...
```

Getting the Reddit credentials, once your access is approved:

1. Go to https://www.reddit.com/prefs/apps → **create another app**.
2. Name: `reddit-legal-ai-prototype`. Type: **script**. Redirect URI: `http://localhost:8080`.
3. The string under the app name is `REDDIT_CLIENT_ID`. The **secret** field is `REDDIT_CLIENT_SECRET`.

Useful optional settings (see `.env.example` for the full list):

| Variable | Default | Purpose |
|---|---|---|
| `SUBREDDITS` | `legaladvice,law,LegalAdviceUK` | Subreddits to read |
| `REDDIT_POST_LIMIT` | `20` | Posts per subreddit |
| `REDDIT_LISTING` | `new` | `new`, `hot` or `rising` |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Claude model |
| `CLAUDE_EFFORT` | `medium` | `low` … `max`. Lower is cheaper and faster |
| `MAX_CLAUDE_CALLS_PER_RUN` | `10` | **Cost guard**: max posts sent to Claude per run |
| `CONFIDENCE_REVIEW_THRESHOLD` | `0.75` | Below this confidence, human review is forced |
| `LEGAL_KEYWORDS` / `EXTRA_LEGAL_KEYWORDS` | built-in list | Replace / extend the keyword filter |
| `MIN_KEYWORD_SCORE` | `1` | Distinct keywords needed to become a candidate |
| `SKIP_ALREADY_PROCESSED` | `true` | Don't pay to re-analyse posts already saved as COMPLETED |
| `LOG_FORMAT` | `text` | `json` for one JSON object per log line |

## 5. Run from the command line

```bash
python run.py
```

Other modes:

```bash
python run.py --test-reddit
```

```bash
python run.py --dry-run
```

```bash
python run.py -s legaladvice -l 5 -m 2
```

`--dry-run` fetches and filters only, so no Anthropic key is needed. `-s`, `-l` and `-m` set the subreddit (repeatable), posts per subreddit, and max Claude calls.

Example output (logs go to stderr, results to stdout):

```
Connecting to Reddit...
Subreddit: legaladvice
Posts retrieved: 20
Total posts: 20
Legal candidates: 5
------------------------------------------------------------------------
Sending candidate to Claude...
Post: Can my landlord keep my deposit?
Link: https://www.reddit.com/r/legaladvice/comments/abc123/...
*** AI-generated preliminary analysis – human review required. ***
AI Classification: LEGAL
Category: Landlord and Tenant / Security Deposit
Confidence: 0.94
Jurisdiction: Unknown
Risk Level: Medium
Question: Can a landlord legally withhold my security deposit?
...
Human Review Required: YES
Review reasons: Flagged for review by AI; Unknown jurisdiction
Result saved:
data\results\2026-10-07_post_abc123.json
```

## 6. Run the FastAPI app

```bash
uvicorn app.main:app --reload
```

(The module path needs the `:app` suffix. Plain `uvicorn app.main` won't start.)

Interactive docs: http://127.0.0.1:8000/docs

| Endpoint | What it does |
|---|---|
| `GET /health` | `{"status": "ok"}` |
| `GET /reddit/test` | Requests a Reddit OAuth token and reports auth mode and scopes |
| `GET /reddit/posts?subreddit=law&limit=5` | Fetches posts and shows each post's keyword-filter decision (no Claude calls) |
| `POST /process` | Runs the full pipeline and returns a summary. Optional JSON body: `{"subreddits": ["legaladvice"], "limit": 5, "max_claude_calls": 2, "dry_run": false}` |

```bash
curl http://127.0.0.1:8000/health
```

```bash
curl -X POST http://127.0.0.1:8000/process -H "Content-Type: application/json" -d "{\"limit\": 5, \"max_claude_calls\": 2}"
```

Example `/process` response:

```json
{
  "total_reddit_posts": 20,
  "legal_candidates": 5,
  "filtered_out": 15,
  "sent_to_claude": 5,
  "successful_analysis": 5,
  "failed_analysis": 0,
  "skipped_already_processed": 0,
  "skipped_over_limit": 0,
  "dry_run": false,
  "aborted_reason": null,
  "subreddit_errors": {},
  "result_files": ["...\\data\\results\\2026-10-07_post_abc123.json"]
}
```

Errors come back as `{"error": "<kind>", "detail": "<safe message>"}` with these status codes: 500 for configuration, 502 for Reddit or Claude auth/API failures, 503 when Reddit rate-limits, and 404 for an unknown subreddit.

`POST /process` runs synchronously and can take a few minutes. A background queue is the obvious next step (see Future architecture).

## 7. Run the tests

```bash
pytest -q
```

The tests never call Reddit or Claude. They use `tests/sample_reddit_posts.json` (fictional posts: landlord deposit, employment dismissal, criminal arrest, non-legal, two ambiguous posts, and immigration) plus mocked clients.

| File | Covers |
|---|---|
| `tests/test_filter.py` | Keyword detection, non-legal filtering, score calculation, custom keywords |
| `tests/test_schemas.py` | `RedditPost` validation, Claude response validation, human-review policy rules |
| `tests/test_claude_client.py` | Request shape, invalid JSON, schema-invalid JSON, refusal, truncation, auth/rate-limit/timeout/network mapping |
| `tests/test_reddit_client.py` | PRAW → typed error mapping, field extraction, config validation |
| `tests/test_pipeline.py` | Collector de-duplication, pipeline end-to-end, failure isolation, auth abort, cost cap, skip-processed, dry run, FastAPI endpoints |

---

## 8. Project architecture

```
Reddit API
   ↓
Reddit Client      app/reddit/client.py       the only module that touches PRAW; returns minimal dicts
   ↓
Collector          app/reddit/collector.py    validates into RedditPost, de-duplicates, per-subreddit errors
   ↓
Legal Filter       app/filtering/legal_filter.py   keywords → FILTERED_OUT / CANDIDATE_FOR_AI
   ↓
Claude Client      app/claude/client.py + prompts.py   structured JSON output, typed errors
   ↓
AI Analysis        app/services/review_policy.py   deterministic rules can only ADD review requirements
   ↓
JSON Storage       app/services/storage.py    data/results/<date>_post_<id>.json
```

`app/services/pipeline.py` orchestrates these stages. Every collaborator is injected, so any stage can be swapped out independently. `app/main.py` (FastAPI) and `app/cli.py` (`run.py`) are thin entry points over the same pipeline.

### Claude integration details

* **Model:** `claude-opus-5-5` by default, with effort set explicitly (`CLAUDE_EFFORT=medium`). Try `low` to cut cost if quality holds on your sample.
* **Structured outputs:** the request sets `output_config.format` to a JSON schema (`ANALYSIS_JSON_SCHEMA` in `prompts.py`), so Claude must return schema-shaped JSON. The response is then validated again with Pydantic (`ClaudeAnalysisResponse`). Invalid JSON or schema failures raise `ClaudeMalformedResponseError`. Only the error position and length are logged, never the content.
* **Refusal fallback (enabled by default):** requests use the beta `fallbacks: "default"` parameter (header `server-side-fallback-2026-07-01`). If a safety classifier declines a request, the API re-runs it on another model server-side. Turn this off with `CLAUDE_ENABLE_FALLBACK=false`. It applies automatically only to models that support it.
* **Prompt caching:** the system prompt is static and marked `cache_control: ephemeral`, so repeated calls in a run can reuse it.
* **Retries:** the SDK retries 429, 5xx and connection errors (`CLAUDE_MAX_RETRIES`). Remaining failures map to `ClaudeAuthError`, `ClaudeRateLimitError`, `ClaudeTimeoutError`, `ClaudeConnectionError`, `ClaudeAPIError` or `ClaudeRefusalError`. One post failing doesn't stop the run. An auth failure stops further Claude calls, because every later call would fail too.

### Human-review policy

`requires_manual_review` is forced to `true`, with reasons recorded in `manual_review_reasons`, when any of these apply:

* the post is legally relevant but the jurisdiction is unknown
* it is a criminal or immigration matter (from Claude's flag **or** the category text)
* it involves serious injury, high financial impact, active litigation, threats or safety risk, unclear facts, or sensitive personal information (Claude's `review_flags`)
* the risk level is High
* confidence is below `CONFIDENCE_REVIEW_THRESHOLD`
* Claude itself asked for review

The policy can add a review requirement but never removes one.

---

## 9. Example Claude prompt

System prompt: see `SYSTEM_PROMPT` in `app/claude/prompts.py`. User turn (from `build_user_prompt`):

```
Analyze the following Reddit post for legal relevance.

<reddit_post>
<subreddit>r/legaladvice</subreddit>
<flair>Landlord/Tenant</flair>
<title>Landlord is keeping my whole security deposit for 'cleaning' - can he do that?</title>
<body>
I moved out of my apartment three weeks ago after a two-year lease. ...
</body>
</reddit_post>

Determine:
1. Is this post legally relevant?
2. What is the primary legal category?
...
13. Explain the classification.

Do not provide definitive legal advice.
```

## 10. Example result file (`data/results/2026-10-07_post_t1dep01.json`)

```json
{
  "reddit_post": {
    "id": "t1dep01",
    "subreddit": "legaladvice",
    "title": "Landlord is keeping my whole security deposit for 'cleaning' - can he do that?",
    "body": "I moved out of my apartment three weeks ago ...",
    "author": "fictional_user_01",
    "created_at": "2026-10-01T09:15:00Z",
    "score": 42,
    "num_comments": 17,
    "permalink": "https://www.reddit.com/r/legaladvice/comments/t1dep01/sample/",
    "url": "https://www.reddit.com/r/legaladvice/comments/t1dep01/sample/",
    "flair": "Landlord/Tenant",
    "nsfw": false
  },
  "filter": { "legal_related": true, "keyword_score": 1, "matched_keywords": ["lease"] },
  "ai_analysis": {
    "relevant": true,
    "confidence": 0.94,
    "legal_category": "Landlord and Tenant",
    "sub_category": "Security Deposit",
    "question": "Can a landlord withhold the full security deposit for cleaning without an itemised list?",
    "summary": "The tenant moved out after a two-year lease and the landlord intends to keep the full $1,800 deposit for cleaning and repainting without itemising deductions.",
    "key_issues": ["Security deposit deductions", "Normal wear and tear", "Itemisation requirements", "Return deadlines"],
    "jurisdiction": "Unknown",
    "jurisdiction_known": false,
    "preliminary_answer": "In many jurisdictions landlords may deduct only for damage beyond normal wear and tear and must provide an itemised statement within a set period; repainting after two years is often treated as wear and tear. The outcome depends on local deposit law, the lease terms and the evidence of the unit's condition.",
    "uncertainty_notes": "No jurisdiction is stated, and deposit rules vary significantly by state and country.",
    "risk_level": "Medium",
    "requires_manual_review": true,
    "review_flags": [],
    "reason": "The post asks a specific question about a landlord's legal right to withhold a deposit.",
    "manual_review_reasons": ["Flagged for review by AI", "Unknown jurisdiction"],
    "model": "claude-opus-5-5",
    "disclaimer": "AI-generated preliminary analysis – human review required."
  },
  "disclaimer": "AI-generated preliminary analysis – human review required.",
  "processed_at": "2026-10-07T10:12:44.120391Z",
  "processing_status": "COMPLETED",
  "error": null
}
```

A failed analysis is saved with `"processing_status": "FAILED"`, `"ai_analysis": null` and a safe `"error"` message.

---

## 11. Security

* `.env` is in `.gitignore`. **Never commit credentials.** Only `.env.example` (placeholders) is tracked.
* Secrets are held as Pydantic `SecretStr`, so they never show up in `repr()`, and configuration errors never echo input values.
* Logs contain IDs, counts, statuses and error types. They never contain secrets, tokens or post bodies. A log filter also masks any configured secret value as a second layer of protection.
* `data/results/` is git-ignored because it holds Reddit content.

**Moving to the cloud later:** don't ship a `.env` file. Load secrets from the platform's secret store at startup and pass them into `Settings`:

* **Azure:** Azure Key Vault plus a Managed Identity for the app (App Service, Container Apps or AKS). Use Key Vault references in app settings, or `azure-identity` with `azure-keyvault-secrets`.
* **AWS:** AWS Secrets Manager plus an IAM role for the task or function (ECS, Lambda or EKS). Use `boto3` `get_secret_value`, or the Secrets Manager integration with ECS/Lambda environment variables.

Either way, `load_settings()` is the single place to change, and the rest of the code receives a `Settings` object. Rotate keys regularly and give each environment its own Reddit app and Anthropic key.

## 12. Future architecture (not implemented)

```
Reddit → Python Collector → PostgreSQL → Pre-Filter → Queue → Claude → AI Analysis
       → Manual Review UI → Reviewer Decision → Reporting
```

Where the current code would plug in:

* **PostgreSQL:** replace `JsonResultStore` with a repository that has the same `save` and `exists` methods. The Pydantic models map directly to tables or JSONB.
* **Queue:** split `LegalContentPipeline.process_posts` at the candidate loop. The collector and filter publish candidates, and a worker calls `process_post`.
* **Batch cost savings:** for non-urgent volume, the Message Batches API runs at about 50% of standard cost.
* **Review UI:** reads `ProcessingResult` records and writes reviewer decisions next to them.
* **Compliance:** Reddit deletion sync, data-retention policy, and audit logging of reviewer actions.
"# reddit-legal-ai" 
