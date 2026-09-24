"""
Freshness & Change Detection — Incremental crawling and content updates.

Handles:
  1. Incremental Crawl Scheduling (priority-based recrawl)
  2. Change Detection (content hash comparison)
  3. Index Merging (real-time + batch)
  4. Content Removal (404s, legal requests, outdated content)
"""

import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger("capybara.freshness")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


# ============================================================
# CRAWL SCHEDULE TRACKER
# ============================================================

class CrawlScheduler:
    """
    Priority-based recrawl scheduling.
    High-quality, frequently-updated sources get crawled more often.
    """

    # Recrawl intervals by update frequency (in hours)
    FREQUENCY_MAP = {
        "hourly": 1,
        "daily": 24,
        "weekly": 168,     # 7 days
        "monthly": 720,    # 30 days
        "quarterly": 2160, # 90 days
        "yearly": 8760,    # 365 days
    }

    def __init__(self):
        self.schedule_file = os.path.join(DATA_DIR, "crawl_schedule.json")
        self.schedule = self._load()

    def _load(self) -> Dict:
        if os.path.exists(self.schedule_file):
            try:
                with open(self.schedule_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"sources": {}, "last_run": None}

    def _save(self):
        with open(self.schedule_file, "w") as f:
            json.dump(self.schedule, f, indent=2, default=str)

    def record_crawl(self, source_name: str, pages_crawled: int,
                     avg_quality: float, errors: int = 0):
        """Record a crawl event for scheduling decisions"""
        now = datetime.now(timezone.utc).isoformat()
        if source_name not in self.schedule["sources"]:
            self.schedule["sources"][source_name] = {
                "crawl_history": [],
                "next_crawl": None,
                "avg_interval_hours": None,
            }

        src = self.schedule["sources"][source_name]
        src["crawl_history"].append({
            "timestamp": now,
            "pages": pages_crawled,
            "avg_quality": avg_quality,
            "errors": errors,
        })

        # Keep last 30 crawl records
        src["crawl_history"] = src["crawl_history"][-30:]

        # Calculate next crawl time
        src["next_crawl"] = self._calculate_next_crawl(src)

        self.schedule["last_run"] = now
        self._save()

    def _calculate_next_crawl(self, src: Dict) -> str:
        """Calculate next crawl time based on quality and freshness signals"""
        history = src.get("crawl_history", [])
        if not history:
            return (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

        # Get average quality across recent crawls
        recent = history[-5:]
        avg_quality = sum(h.get("avg_quality", 0.5) for h in recent) / len(recent)
        avg_errors = sum(h.get("errors", 0) for h in recent) / len(recent)

        # Base interval: quality-dependent
        if avg_quality >= 0.9:
            base_hours = 24      # High quality = daily
        elif avg_quality >= 0.7:
            base_hours = 72      # Medium = every 3 days
        elif avg_quality >= 0.5:
            base_hours = 168     # Low-medium = weekly
        else:
            base_hours = 720     # Low = monthly

        # Error penalty: more errors = longer interval
        if avg_errors > 5:
            base_hours *= 2
        elif avg_errors > 2:
            base_hours *= 1.5

        # Recency: if pages are changing frequently, crawl more
        if len(history) >= 3:
            page_counts = [h.get("pages", 0) for h in history[-3:]]
            if len(set(page_counts)) > 1:  # Pages changing
                base_hours = max(base_hours * 0.5, 12)  # At least every 12 hours

        next_time = datetime.now(timezone.utc) + timedelta(hours=base_hours)
        return next_time.isoformat()

    def get_due_sources(self) -> List[str]:
        """Get sources that are due for recrawl"""
        now = datetime.now(timezone.utc)
        due = []
        for name, src in self.schedule.get("sources", {}).items():
            next_crawl = src.get("next_crawl")
            if not next_crawl:
                due.append(name)
                continue
            try:
                next_dt = datetime.fromisoformat(next_crawl.replace("Z", "+00:00"))
                if now >= next_dt:
                    due.append(name)
            except (ValueError, TypeError):
                due.append(name)
        return due

    def get_stats(self) -> Dict:
        """Get scheduling statistics"""
        sources = self.schedule.get("sources", {})
        now = datetime.now(timezone.utc)
        due = self.get_due_sources()

        return {
            "total_sources": len(sources),
            "due_for_crawl": len(due),
            "last_run": self.schedule.get("last_run"),
            "due_sources": due[:20],
        }


# ============================================================
# CHANGE DETECTOR
# ============================================================

class ChangeDetector:
    """
    Detects content changes between crawls.
    Uses content hashing to identify modified pages.
    """

    def __init__(self):
        self.changes_file = os.path.join(DATA_DIR, "content_changes.json")
        self.changes = self._load()

    def _load(self) -> Dict:
        if os.path.exists(self.changes_file):
            try:
                with open(self.changes_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_check": None, "changes": [], "stats": {"updated": 0, "new": 0, "removed": 0}}

    def _save(self):
        # Keep last 10000 change records
        self.changes["changes"] = self.changes["changes"][-10000:]
        with open(self.changes_file, "w") as f:
            json.dump(self.changes, f, indent=2, default=str)

    def detect_changes(self, url: str, new_content_hash: str,
                       old_content_hash: Optional[str] = None) -> Dict:
        """
        Detect if content has changed.
        Returns change metadata.
        """
        if old_content_hash is None:
            change_type = "new"
        elif old_content_hash != new_content_hash:
            change_type = "updated"
        else:
            change_type = "unchanged"

        if change_type != "unchanged":
            change = {
                "url": url,
                "type": change_type,
                "old_hash": old_content_hash,
                "new_hash": new_content_hash,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.changes["changes"].append(change)
            self.changes["stats"][change_type] = self.changes["stats"].get(change_type, 0) + 1
            self.changes["last_check"] = datetime.now(timezone.utc).isoformat()
            self._save()

        return {
            "changed": change_type != "unchanged",
            "type": change_type,
        }

    def get_change_stats(self) -> Dict:
        """Get change detection statistics"""
        return {
            "total_changes": len(self.changes.get("changes", [])),
            "stats": self.changes.get("stats", {}),
            "last_check": self.changes.get("last_check"),
        }


# ============================================================
# CONTENT REMOVAL MANAGER
# ============================================================

class ContentRemovalManager:
    """
    Handles content removal: 404s, legal requests, outdated content.
    """

    def __init__(self):
        self.removal_file = os.path.join(DATA_DIR, "content_removals.json")
        self.removals = self._load()

    def _load(self) -> Dict:
        if os.path.exists(self.removal_file):
            try:
                with open(self.removal_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"urls": {}, "pending": [], "completed": []}

    def _save(self):
        with open(self.removal_file, "w") as f:
            json.dump(self.removals, f, indent=2, default=str)

    def mark_for_removal(self, url: str, reason: str):
        """Mark a URL for removal"""
        self.removals["urls"][url] = {
            "reason": reason,
            "marked_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }
        self.removals["pending"].append(url)
        self._save()

    def handle_404(self, url: str):
        """Handle 404 response by marking for removal"""
        self.mark_for_removal(url, "404_not_found")

    def is_removed(self, url: str) -> bool:
        """Check if URL is marked for removal"""
        return url in self.removals.get("urls", {})

    def get_pending_removals(self) -> List[str]:
        """Get URLs pending removal"""
        return self.removals.get("pending", [])

    def get_stats(self) -> Dict:
        """Get removal statistics"""
        return {
            "total_marked": len(self.removals.get("urls", {})),
            "pending": len(self.removals.get("pending", [])),
            "completed": len(self.removals.get("completed", [])),
        }


# ============================================================
# FRESHNESS SCORER
# ============================================================

class FreshnessScorer:
    """
    Scores content freshness based on publication date,
    update frequency, and change history.
    """

    def score(self, published_date: str, change_freq: str = "weekly",
              last_modified: str = "") -> float:
        """Calculate freshness score 0-1"""
        now = datetime.now(timezone.utc)

        # Try to get publication date
        pub_dt = self._parse_date(published_date)
        if pub_dt:
            days_old = (now - pub_dt).days
            # Exponential decay: halves every 180 days
            age_score = 0.5 ** (days_old / 180)
        else:
            age_score = 0.5  # Unknown = middle score

        # Change frequency bonus
        freq_map = {"hourly": 1.0, "daily": 0.9, "weekly": 0.7,
                     "monthly": 0.5, "quarterly": 0.3, "yearly": 0.1}
        freq_score = freq_map.get(change_freq, 0.5)

        # Last modified bonus
        mod_dt = self._parse_date(last_modified)
        if mod_dt:
            days_since_mod = (now - mod_dt).days
            mod_score = 0.5 ** (days_since_mod / 90)  # Faster decay
        else:
            mod_score = 0.5

        # Weighted combination
        return round(
            0.5 * age_score + 0.25 * freq_score + 0.25 * mod_score,
            4
        )

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date string"""
        if not date_str:
            return None

        for fmt in [
            "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
            "%Y/%m/%d", "%d/%m/%Y",
        ]:
            try:
                dt = datetime.strptime(date_str[:len(fmt) + 5], fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue

        # Try year extraction
        import re
        year_match = re.search(r'(20\d{2})', date_str)
        if year_match:
            year = int(year_match.group(1))
            return datetime(year, 6, 1, tzinfo=timezone.utc)

        return None
