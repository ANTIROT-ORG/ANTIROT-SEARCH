"""
HuggingFace Dataset Uploader
Stores crawled and filtered content as HuggingFace datasets.
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("capybara.hf_uploader")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class HFUploader:
    """
    Uploads curated human knowledge to HuggingFace datasets.
    Each category becomes a dataset split.
    """

    def __init__(self, repo_name: Optional[str] = None):
        self.repo_name = repo_name or os.environ.get(
            "HF_DATASET_REPO", "TheBoringRats/ratsearch-knowledge"
        )
        self.token = os.environ.get("HF_TOKEN")

    def _load_all_filtered(self) -> Dict[str, List[Dict]]:
        """Load all filtered content grouped by category"""
        import glob

        categories = {}
        data_files = sorted(glob.glob(os.path.join(DATA_DIR, "filtered_*.jsonl")))

        for data_file in data_files:
            with open(data_file, "r") as f:
                for line in f:
                    if line.strip():
                        item = json.loads(line)
                        cat = item.get("source_category", "unknown")
                        if cat not in categories:
                            categories[cat] = []
                        categories[cat].append(item)

        return categories

    def _prepare_dataset_rows(self, items: List[Dict]) -> List[Dict]:
        """Prepare items for HuggingFace dataset format"""
        rows = []
        for item in items:
            rows.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "source_name": item.get("source_name", ""),
                "source_category": item.get("source_category", ""),
                "content_text": item.get("content_text", ""),
                "author": item.get("author"),
                "published_date": item.get("published_date"),
                "language": item.get("language", "en"),
                "word_count": item.get("word_count", 0),
                "quality_score": item.get("quality_score", 0),
                "content_hash": item.get("content_hash", ""),
                "crawl_timestamp": item.get("crawl_timestamp", ""),
            })
        return rows

    def upload(self) -> Dict:
        """Upload content to HuggingFace"""
        if not self.token:
            logger.error("HF_TOKEN not set. Cannot upload to HuggingFace.")
            return {"success": False, "error": "HF_TOKEN not set"}

        try:
            from huggingface_hub import HfApi
            api = HfApi(token=self.token)

            # Load filtered content
            categories = self._load_all_filtered()

            if not categories:
                logger.warning("No filtered content to upload")
                return {"success": False, "error": "No content to upload"}

            # Create or update repo
            try:
                api.create_repo(
                    repo_id=self.repo_name,
                    repo_type="dataset",
                    exist_ok=True
                )
            except Exception as e:
                logger.warning(f"Repo creation note: {e}")

            total_uploaded = 0

            for category, items in categories.items():
                logger.info(f"Uploading {len(items)} items for category: {category}")

                rows = self._prepare_dataset_rows(items)

                # Save as parquet for efficiency
                import pandas as pd
                df = pd.DataFrame(rows)

                parquet_path = os.path.join(DATA_DIR, f"upload_{category}.parquet")
                df.to_parquet(parquet_path, index=False)

                # Upload to HuggingFace
                api.upload_file(
                    path_or_fileobj=parquet_path,
                    path_in_repo=f"{category}/data.parquet",
                    repo_id=self.repo_name,
                    repo_type="dataset",
                    commit_message=f"Update {category}: {len(rows)} items ({datetime.utcnow().isoformat()})"
                )

                total_uploaded += len(rows)
                os.remove(parquet_path)

            # Upload metadata
            meta = {
                "last_updated": datetime.utcnow().isoformat(),
                "total_items": total_uploaded,
                "categories": {k: len(v) for k, v in categories.items()},
            }

            meta_path = os.path.join(DATA_DIR, "hf_metadata.json")
            with open(meta_path, "w") as f:
                json.dump(meta, f, indent=2)

            api.upload_file(
                path_or_fileobj=meta_path,
                path_in_repo="metadata.json",
                repo_id=self.repo_name,
                repo_type="dataset",
                commit_message=f"Update metadata: {total_uploaded} total items"
            )

            logger.info(f"Uploaded {total_uploaded} items to {self.repo_name}")

            return {
                "success": True,
                "repo": self.repo_name,
                "total_uploaded": total_uploaded,
                "categories": {k: len(v) for k, v in categories.items()},
            }

        except ImportError:
            logger.error("huggingface_hub not installed. pip install huggingface_hub")
            return {"success": False, "error": "huggingface_hub not installed"}
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            return {"success": False, "error": str(e)}


async def main():
    """Run HuggingFace upload"""
    uploader = HFUploader()
    result = uploader.upload()

    if result["success"]:
        print(f"✅ Uploaded {result['total_uploaded']} items to {result['repo']}")
        for cat, count in result["categories"].items():
            print(f"   {cat}: {count} items")
    else:
        print(f"❌ Upload failed: {result['error']}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
