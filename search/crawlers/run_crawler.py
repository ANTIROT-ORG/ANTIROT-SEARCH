"""
Crawler Runner — Entry point for GitHub Actions and local execution
"""

import asyncio
import os
import sys

from . import KnowledgeCrawler


async def main():
    category = os.environ.get("CRAWL_CATEGORY", "all")
    priority = int(os.environ.get("CRAWL_PRIORITY", "1"))

    print(f"🕷️ Antirot Crawler")
    print(f"   Category: {category}")
    print(f"   Priority: ≤{priority}")
    print()

    crawler = KnowledgeCrawler(max_concurrent=5)

    try:
        stats = await crawler.run(category=category, priority=priority)

        print()
        print(f"📊 Crawl Complete")
        print(f"   Pages: {stats['total_pages']}")
        print(f"   Sources: {stats['crawled_sources']}/{stats['total_sources']}")
        print(f"   Avg rank: {stats['avg_rank']}")
        print(f"   Media found: {stats['media_total']}")
        print(f"   Duration: {stats['duration_seconds']}s")

        if stats.get("errors"):
            print(f"   ⚠️  {len(stats['errors'])} source errors")

        return 0

    finally:
        await crawler.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
