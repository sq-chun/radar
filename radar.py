import os
import re
import time
import hmac
import hashlib
import base64
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

# ================= 环境变量配置 =================
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# Lark (国际版飞书) 配置
LARK_WEBHOOK = os.getenv("LARK_WEBHOOK", "")
LARK_SECRET = os.getenv("LARK_SECRET", "")

# Telegram 配置 (可选)
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

STOP_WORDS = {
    "the", "to", "of", "and", "a", "in", "for", "is", "on", "that", "by",
    "this", "with", "i", "you", "it", "not", "or", "be", "are", "from",
    "at", "as", "your", "all", "have", "new", "more", "an", "was", "we",
    "how", "what", "why", "show", "hn", "launch", "using", "app", "ai",
    "can", "will", "just", "now", "get", "my", "our", "out", "up", "about",
    "open", "source", "tool", "tools", "project", "based", "build", "built"
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ================= 1. 四大早期信号源采集 =================
def fetch_all_sources():
    items = []

    # 1) Hacker News Top 100
    try:
        hn_ids = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10).json()[:100]
        for item_id in hn_ids:
            item = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", timeout=5).json()
            if item and "title" in item:
                items.append(f"[HN] {item['title']}")
    except Exception as e:
        print(f"HN 抓取异常: {e}")

    # 2) Reddit Rising 板块
    for sub in ["SaaS", "SideProject", "Entrepreneur", "LocalLLaMA", "webdev"]:
        try:
            url = f"https://www.reddit.com/r/{sub}/rising.json?limit=40"
            res = requests.get(url, headers={"User-Agent": "TrendRadarBot/1.0"}, timeout=10).json()
            for post in res.get("data", {}).get("children", []):
                items.append(f"[Reddit r/{sub}] {post['data']['title']}")
        except Exception as e:
            print(f"Reddit r/{sub} 抓取异常: {e}")

    # 3) GitHub Trending (每日趋势榜)
    try:
        res = requests.get("https://github.com/trending?since=daily", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        for article in soup.select("article.Box-row")[:25]:
            repo_tag = article.select_one("h2 a")
            desc_tag = article.select_one("p")
            repo_name = repo_tag.get_text(strip=True).replace(" ", "") if repo_tag else ""
            desc = desc_tag.get_text(strip=True) if desc_tag else ""
            if repo_name:
                items.append(f"[GitHub] {repo_name}: {desc}")
    except Exception as e:
        print(f"GitHub Trending 抓取异常: {e}")

    # 4) Product Hunt 每日新品 (官方公开 RSS)
    try:
        res = requests.get("https://www.producthunt.com/feed", headers=HEADERS, timeout=15)
        root = ET.fromstring(res.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns)[:30]:
            title_el = entry.find("atom:title", ns)
            if title_el is not None and title_el.text:
                items.append(f"[ProductHunt] {title_el.text.strip()}")
    except Exception as e:
        print(f"Product Hunt 抓取异常: {e}")

    return items


# ================= 2. 词频统计与上下文提取 =================
def extract_ngrams_with_context(items):
    counter = Counter()
    word_contexts = defaultdict(list)
    word_sources = defaultdict(set)

    for full_title in items:
        source_match = re.match(r"^\[(.*?)\]", full_title)
        source_name = source_match.group(1).split()[0] if source_match else "Unknown"
        clean_title = re.sub(r"^\[.*?\]\s*", "", full_title)

        words = [w for w in re.findall(r"[a-z0-9\-]+", clean_title.lower()) if w not in STOP_WORDS and len(w) > 2]
        ngrams = set(words)
        bigrams = {f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)}
        all_terms = ngrams.union(bigrams)

        for term in all_terms:
            counter[term] += 1
            word_sources[term].add(source_name)
            if len(word_contexts[term]) < 4:
                word_contexts[term].append(full_title)

    return counter, word_contexts, word_sources


# ================= 3. 异常检测（含跨平台加权） =================
def detect_anomalies(cur, today_str, today_counts, word_sources):
    past_7_days = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    anomalies = []

    for word, today_cnt in today_counts.items():
        if today_cnt < 3:
            continue
        cur.execute("""
            SELECT AVG(count) FROM word_stats 
            WHERE word = ? AND date >= ? AND date < ?
        """, (word, past_7_days, today_str))
        avg_past = cur.fetchone()[0] or 0

        if avg_past == 0 or (today_cnt / avg_past) >= 2.5:
            anomalies.append({
                "word": word,
                "today": today_cnt,
                "avg_7d": round(avg_past, 2),
                "sources": list(word_sources[word]),
                "score": today_cnt * (1.5 if len(word_sources[word]) >= 2 else 1.0)
            })

    return sorted(anomalies, key=lambda x: x["score"], reverse=True)[:22]


# ================= 4. 大模型降噪与商机分析 =================
def analyze_with_llm(anomalies, word_contexts):
    if not anomalies:
        return "今日暂无显著的早期趋势异动。"
    if not LLM_API_KEY:
        lines = [f"• **{a['word']}** (今日:{a['today']}次 / 来源:{','.join(a['sources'])})" for a in anomalies[:10]]
        return "未配置 LLM_API_KEY，以下为原始异动词：\n" + "\n".join(lines)

    payload_text = ""
    for item in anomalies:
        w = item["word"]
        ctx = " | ".join(word_contexts[w])
        srcs = ", ".join(item["sources"])
        payload_text += f"- 词条: `{w}` (今日 {item['today']} 次, 7日均 {item['avg_7d']}, 来源平台: {srcs})\n  帖子上下文: {ctx}\n"

    prompt = f"""你是一个敏锐的独立开发者和 SaaS 市场分析师。
以下是我从 Hacker News、Reddit、GitHub Trending 和 Product Hunt 抓取的【今日词频异常飙升词条】及其上下文：

{payload_text}

请执行以下任务：
1. **严格降噪**：剔除通用词、日常动作词或泛概念词。
2. **优先跨平台信号**：重点关注同时在多个平台（如 GitHub + HN 或 Reddit + ProductHunt）冒头的新技术、新协议、新工具或具体用户痛点。
3. **输出 3-6 个高价值信号**，使用简洁的中文 Markdown 格式，每条包含：
   - **关键词**（标注来源平台与今日频次）
   - **早期信号解读**：结合标题，一句话说明为什么它正在被讨论。
   - **全栈切入机会**：有没有适合独立开发者的 Micro-SaaS、浏览器插件、API 封装或内容工具机会？（无明显商业机会则注明“纯技术追踪”）
"""

    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }

    try:
        res = requests.post(f"{LLM_BASE_URL.rstrip('/')}/chat/completions", headers=headers, json=data, timeout=45)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"LLM 分析请求失败: {e}"


# ================= 5. Lark & Telegram Webhook 推送 =================
def gen_lark_sign(timestamp, secret):
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_to_lark(title, markdown_content):
    if not LARK_WEBHOOK:
        return

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue"
            },
            "elements": [
                {"tag": "markdown", "content": markdown_content}
            ]
        }
    }

    if LARK_SECRET:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = gen_lark_sign(timestamp, LARK_SECRET)

    try:
        res = requests.post(LARK_WEBHOOK, json=payload, timeout=10)
        result = res.json()
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            print("Lark 推送成功！")
        else:
            print(f"Lark 推送失败: {result}")
    except Exception as e:
        print(f"Lark 推送请求异常: {e}")


def send_to_telegram(title, markdown_content):
    if not (TG_BOT_TOKEN and TG_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    text = f"*{title}*\n\n{markdown_content}"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text[:4000], "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Telegram 推送请求异常: {e}")


if __name__ == "__main__":
    conn = sqlite3.connect("trends.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS word_stats (
            date TEXT,
            word TEXT,
            count INTEGER,
            PRIMARY KEY (date, word)
        )
    """)
    conn.commit()

    today = datetime.now().strftime("%Y-%m-%d")
    items = fetch_all_sources()
    print(f"[{today}] 四源合计抓取 {len(items)} 条数据。")

    counts, contexts, sources = extract_ngrams_with_context(items)
    for word, cnt in counts.items():
        cur.execute("INSERT OR REPLACE INTO word_stats VALUES (?, ?, ?)", (today, word, cnt))
    conn.commit()

    anomalies = detect_anomalies(cur, today, counts, sources)
    report = analyze_with_llm(anomalies, contexts)

    report_title = f"📡 早期需求与趋势雷达 ({today})"
    print("\n" + report_title + "\n" + report)

    send_to_lark(report_title, report)
    send_to_telegram(report_title, report)
    conn.close()
