import os
import sys
import requests

API_KEY = os.environ.get("TAVILY_API_KEY")

def tavily_search(query, max_results=3):
    resp = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": API_KEY,
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
        },
    )
    resp.raise_for_status()
    return resp.json()

if __name__ == "__main__":
    if not API_KEY:
        print("Error: TAVILY_API_KEY environment variable is not set.")
        sys.exit(1)

    results = tavily_search("What is Tavily?")
    for r in results["results"]:
        print(f"Title: {r['title']}")
        print(f"URL: {r['url']}")
        print(f"Content: {r['content'][:200]}...")
        print()

    credits_used = 1
    cost_per_credit = 0.008
    print(f"Credits used: {credits_used}")
    print(f"Cost: ${credits_used * cost_per_credit:.4f}")
