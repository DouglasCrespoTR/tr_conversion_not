#!/usr/bin/env python
"""
ADO Knowledge Builder - Azure DevOps Work Items + GitHub PRs -> Knowledge Base

Queries ADO for resolved/closed Work Items with Git links, fetches PR details
from GitHub, and builds a structured knowledge base for the TAX ONE support pipeline.

Usage:
  python ado_knowledge_builder.py --rebuild
  python ado_knowledge_builder.py --rebuild --dry-run
  python ado_knowledge_builder.py --update
  python ado_knowledge_builder.py --stats
  python ado_knowledge_builder.py --rebuild --from-date 2025-01-01
  python ado_knowledge_builder.py --rebuild --no-cache      # Force re-fetch all PRs
  python ado_knowledge_builder.py --rebuild --workers 4     # Limit parallelism

Credentials: $ADO_PAT (Azure DevOps), gh CLI authenticated (GitHub)
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def log(msg=""):
    """Print with flush for real-time output."""
    print(msg, flush=True)


# --- Configuration ---

ADO_ORG = "https://dev.azure.com/tr-ggo"
ADO_PROJECT = "Mastersaf Fiscal Solutions"
ADO_PROJECT_ENC = "Mastersaf%20Fiscal%20Solutions"
ADO_AREA_PATH = "Mastersaf Fiscal Solutions\\MFS\\TAX ONE\\Suporte"
ADO_API_VERSION = "7.1"

RATE_LIMIT_DELAY = 0.35  # ~170 req/min (safe margin under 200/min)
BATCH_SIZE = 200  # ADO max per request

DEFAULT_OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "knowledge", "ado-fixes"
)

METADATA_FILE = "_metadata.json"
PR_CACHE_FILE = "_pr_cache.json"

DEFAULT_WORKERS = 8  # Parallel workers for GitHub/ADO fetches
ADO_WORKERS = 4  # Parallel workers for ADO batch fetches
ADO_MAX_REQ_PER_MIN = 150  # Safe margin under 200/min ADO limit

# Vertical normalization from Area Path segments
VERTICAL_MAP = {
    "basicos": "basicos",
    "básicos": "basicos",
    "sped": "sped",
    "estadual": "estadual",
    "federal": "federal",
    "municipal": "municipal",
    "municpal": "municipal",
    "especificos": "especificos",
    "específicos": "especificos",
    "utilitarios": "utilitarios",
    "utilitários": "utilitarios",
}

# GitHub repos that may appear in ADO Git links
GH_REPOS = {
    "taxone_dw": "tr/taxone_dw",
    "taxone_modules_t1dw": "tr/taxone_modules_t1dw",
    "taxone_modules_t1inre": "tr/taxone_modules_t1inre",
    "taxone_modules_t1piscofins": "tr/taxone_modules_t1piscofins",
    "taxone_dw_conteudo": "tr/taxone_dw_conteudo",
}
DEFAULT_GH_REPO = "tr/taxone_dw"


# --- HTML Stripper ---

class HTMLStripper(HTMLParser):
    """Strip HTML tags and convert basic elements to plain text."""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._in_li = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "br":
            self._parts.append("\n")
        elif tag == "p":
            self._parts.append("\n")
        elif tag == "li":
            self._parts.append("\n- ")
            self._in_li = True
        elif tag in ("h1", "h2", "h3", "h4"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "p":
            self._parts.append("\n")
        elif tag == "li":
            self._in_li = False
        elif tag in ("h1", "h2", "h3", "h4"):
            self._parts.append("\n")

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        text = "".join(self._parts)
        # Collapse multiple blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def strip_html(html_text):
    """Convert HTML to plain text."""
    if not html_text:
        return ""
    stripper = HTMLStripper()
    try:
        stripper.feed(html_text)
        return stripper.get_text()
    except Exception:
        # Fallback: regex strip
        text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return text.strip()


# --- ADO Client ---

class ADOClient:
    """Azure DevOps REST API client."""

    def __init__(self, workers=ADO_WORKERS):
        pat = os.environ.get("ADO_PAT", "")
        if not pat:
            log("ERROR: ADO_PAT environment variable not set")
            sys.exit(1)
        self.auth_header = "Basic " + base64.b64encode(f":{pat}".encode()).decode()
        self.base_url = f"{ADO_ORG}/{ADO_PROJECT_ENC}/_apis"
        self.workers = workers
        # Rate limiter: controls requests across all threads
        self._rate_lock = threading.Lock()
        self._min_interval = 60.0 / ADO_MAX_REQ_PER_MIN  # ~0.4s between requests
        self._last_request_time = 0.0

    def _rate_wait(self):
        """Wait to respect ADO rate limit across all threads."""
        with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    def _request(self, url, method="GET", body=None, content_type="application/json"):
        """Make authenticated request to ADO API."""
        self._rate_wait()
        req = Request(url, method=method)
        req.add_header("Authorization", self.auth_header)
        req.add_header("Accept", "application/json")

        data = None
        if body is not None:
            req.add_header("Content-Type", content_type)
            data = json.dumps(body).encode("utf-8")

        try:
            with urlopen(req, data=data, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", 10))
                log(f"  Rate limited. Waiting {retry_after}s...")
                time.sleep(retry_after)
                return self._request(url, method, body, content_type)
            # Read error body and attach to exception for callers
            try:
                e._error_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                e._error_body = ""
            raise
        except URLError as e:
            log(f"  Network error: {e.reason} - {url}")
            raise

    def wiql_query(self, wiql_text):
        """Execute WIQL query and return list of Work Item IDs."""
        url = f"{self.base_url}/wit/wiql?api-version={ADO_API_VERSION}"
        try:
            result = self._request(url, method="POST", body={"query": wiql_text})
            return [wi["id"] for wi in result.get("workItems", [])]
        except HTTPError as e:
            if e.code == 400:
                err_body = getattr(e, "_error_body", "")
                if "20000" in err_body or "size limit" in err_body.lower():
                    log("    (query exceeded 20k limit, will segment)")
                    return None  # Signal to caller to segment
                log(f"    WIQL error: {err_body[:300]}")
            raise

    def _fetch_batch(self, batch_info, expand, total):
        """Fetch a single batch of work items. Thread-safe."""
        i, batch = batch_info
        ids_str = ",".join(str(x) for x in batch)
        url = (
            f"{self.base_url}/wit/workitems"
            f"?ids={ids_str}&$expand={expand}&api-version={ADO_API_VERSION}"
        )
        try:
            result = self._request(url)
            return result.get("value", [])
        except HTTPError as e:
            log(f"  WARNING: batch {i+1} failed (HTTP {e.code}), retrying after 5s...")
            time.sleep(5)
            try:
                result = self._request(url)
                return result.get("value", [])
            except HTTPError:
                log(f"  ERROR: batch {i+1} failed again, skipping")
                return []

    def _fetch_parallel(self, ids, expand, label):
        """Parallel batch fetch using ThreadPoolExecutor."""
        batches = []
        for i in range(0, len(ids), BATCH_SIZE):
            batches.append((i, ids[i:i + BATCH_SIZE]))

        if len(batches) <= 2 or self.workers <= 1:
            # Not worth parallelizing for small counts
            all_items = []
            for batch_info in batches:
                i, batch = batch_info
                if (i // BATCH_SIZE) % 20 == 0:
                    log(f"  {label} {i+1}-{i+len(batch)} of {len(ids)}...")
                all_items.extend(self._fetch_batch(batch_info, expand, len(ids)))
            return all_items

        log(f"  {label} {len(ids)} items in {len(batches)} batches ({self.workers} workers)...")
        all_items = []
        completed = 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(self._fetch_batch, batch_info, expand, len(ids)): batch_info
                for batch_info in batches
            }
            for future in as_completed(futures):
                items = future.result()
                all_items.extend(items)
                completed += 1
                if completed % 20 == 0 or completed == len(batches):
                    log(f"  {label} batches: {completed}/{len(batches)} done ({len(all_items)} items)")
        return all_items

    def fetch_workitems_relations(self, ids):
        """Batch fetch Work Item IDs + relations only (lightweight).
        Returns list of WI dicts with id and relations."""
        return self._fetch_parallel(ids, "relations", "Scanning relations")

    def fetch_workitems_full(self, ids):
        """Batch fetch full Work Item details. Returns list of WI dicts."""
        return self._fetch_parallel(ids, "all", "Fetching details")


# --- GitHub Client (via gh CLI) ---

class GitHubClient:
    """GitHub client using gh CLI with batched API calls."""

    def __init__(self, default_repo=DEFAULT_GH_REPO, workers=DEFAULT_WORKERS,
                 cache_dir=None, use_cache=True):
        self.default_repo = default_repo
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._repo_owner, self._repo_name = default_repo.split("/")
        self.workers = workers
        self.use_cache = use_cache
        self._cache_dir = cache_dir
        self._graphql_batch_size = 50  # Increased from 20

    def _load_pr_cache(self):
        """Load PR cache from disk."""
        if not self.use_cache or not self._cache_dir:
            return
        cache_path = os.path.join(self._cache_dir, PR_CACHE_FILE)
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    disk_cache = json.load(f)
                loaded = 0
                for key, value in disk_cache.items():
                    if key not in self._cache:
                        self._cache[key] = value
                        loaded += 1
                log(f"    Loaded {loaded} PRs from disk cache")
            except (json.JSONDecodeError, OSError) as e:
                log(f"    WARNING: Could not load PR cache: {e}")

    def _save_pr_cache(self):
        """Save PR cache to disk."""
        if not self.use_cache or not self._cache_dir:
            return
        cache_path = os.path.join(self._cache_dir, PR_CACHE_FILE)
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            # Only save non-None entries (found PRs)
            to_save = {k: v for k, v in self._cache.items() if v is not None}
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(to_save, f, ensure_ascii=False)
            log(f"    Saved {len(to_save)} PRs to disk cache")
        except OSError as e:
            log(f"    WARNING: Could not save PR cache: {e}")

    def _fetch_graphql_batch(self, batch, owner, name, repo):
        """Fetch a single GraphQL batch of PRs. Thread-safe."""
        queries = []
        for idx, pr_num in enumerate(batch):
            queries.append(
                f'pr{idx}: pullRequest(number: {pr_num}) {{ '
                f'number title body state mergedAt additions deletions '
                f'files(first: 100) {{ nodes {{ path additions deletions }} }} '
                f'}}'
            )
        query = f'{{ repository(owner: "{owner}", name: "{name}") {{ {" ".join(queries)} }} }}'

        try:
            result = subprocess.run(
                ["gh", "api", "graphql", "-f", f"query={query}"],
                capture_output=True, text=True, timeout=60,
                encoding="utf-8", errors="replace"
            )
            if result.returncode == 0 and result.stdout:
                resp = json.loads(result.stdout)
                repo_data = resp.get("data", {}).get("repository", {})
                results = {}
                for idx, pr_num in enumerate(batch):
                    pr_data = repo_data.get(f"pr{idx}")
                    if pr_data and pr_data.get("number"):
                        files = pr_data.get("files", {}).get("nodes", [])
                        results[f"pr#{pr_num}"] = {
                            "number": pr_data["number"],
                            "title": pr_data.get("title", ""),
                            "body": pr_data.get("body", ""),
                            "state": pr_data.get("state", ""),
                            "mergedAt": pr_data.get("mergedAt", ""),
                            "additions": pr_data.get("additions", 0),
                            "deletions": pr_data.get("deletions", 0),
                            "files": [
                                {"path": f.get("path", ""),
                                 "additions": f.get("additions", 0),
                                 "deletions": f.get("deletions", 0)}
                                for f in files
                            ],
                            "_repo": repo,
                        }
                    else:
                        results[f"pr#{pr_num}"] = None
                return results
            else:
                if result.stderr and "Could not resolve" in result.stderr:
                    return {f"pr#{pr_num}": None for pr_num in batch}
                # Complexity error — retry with smaller batch
                if result.stderr and "complexity" in result.stderr.lower():
                    if len(batch) > 20:
                        mid = len(batch) // 2
                        r1 = self._fetch_graphql_batch(batch[:mid], owner, name, repo)
                        r2 = self._fetch_graphql_batch(batch[mid:], owner, name, repo)
                        r1.update(r2)
                        return r1
                # Fallback to individual
                results = {}
                for pr_num in batch:
                    key = f"pr#{pr_num}"
                    pr = self._get_pr_individual(pr_num, repo)
                    results[key] = pr
                return results
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            log(f"    GraphQL batch error: {e}, falling back to individual")
            results = {}
            for pr_num in batch:
                key = f"pr#{pr_num}"
                pr = self._get_pr_individual(pr_num, repo)
                results[key] = pr
            return results

    def prefetch_prs(self, pr_numbers, repo=None):
        """Batch fetch PR details using gh api with GraphQL.
        Uses ThreadPoolExecutor for parallel fetching."""
        repo = repo or self.default_repo
        owner, name = repo.split("/")

        # Load disk cache on first call
        if not self._cache and self.use_cache:
            self._load_pr_cache()

        uncached = [n for n in set(pr_numbers) if f"pr#{n}" not in self._cache]

        if not uncached:
            return

        log(f"    Prefetching {len(uncached)} unique PRs from {repo}...")

        # Build batches
        batch_size = self._graphql_batch_size
        batches = [uncached[i:i + batch_size] for i in range(0, len(uncached), batch_size)]

        if len(batches) <= 2 or self.workers <= 1:
            # Sequential for small counts
            for i, batch in enumerate(batches):
                if i % 25 == 0 and i > 0:
                    log(f"    Prefetched {i * batch_size}/{len(uncached)} PRs...")
                results = self._fetch_graphql_batch(batch, owner, name, repo)
                with self._cache_lock:
                    self._cache.update(results)
        else:
            # Parallel fetch
            log(f"    Using {self.workers} workers for {len(batches)} batches...")
            completed = 0
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = {
                    pool.submit(self._fetch_graphql_batch, batch, owner, name, repo): batch
                    for batch in batches
                }
                for future in as_completed(futures):
                    results = future.result()
                    with self._cache_lock:
                        self._cache.update(results)
                    completed += 1
                    if completed % 25 == 0 or completed == len(batches):
                        log(f"    Batches: {completed}/{len(batches)} done ({len(self._cache)} cached)")

        # Save cache after fetch
        self._save_pr_cache()

        still_uncached = [n for n in uncached if f"pr#{n}" not in self._cache]
        if still_uncached:
            log(f"    {len(still_uncached)} PRs not found in {repo}, trying alternate repos...")
            for alt_repo in GH_REPOS.values():
                if alt_repo != repo and still_uncached:
                    self.prefetch_prs(still_uncached, alt_repo)
                    still_uncached = [n for n in still_uncached if f"pr#{n}" not in self._cache]

    def _get_pr_individual(self, pr_number, repo):
        """Fallback: fetch single PR via gh pr view."""
        cache_key = f"pr#{pr_number}"
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        try:
            result = subprocess.run(
                [
                    "gh", "pr", "view", str(pr_number),
                    "--repo", repo,
                    "--json", "number,title,body,files,additions,deletions,mergedAt,state"
                ],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace"
            )
            if result.returncode == 0 and result.stdout:
                data = json.loads(result.stdout)
                data["_repo"] = repo
                with self._cache_lock:
                    self._cache[cache_key] = data
                return data
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass
        with self._cache_lock:
            self._cache[cache_key] = None
        return None

    def get_pr(self, pr_number, repo=None):
        """Get PR from cache (should be prefetched). Falls back to individual fetch."""
        cache_key = f"pr#{pr_number}"
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        return self._get_pr_individual(pr_number, repo or self.default_repo)


# --- Knowledge Builder ---

class KnowledgeBuilder:
    """Builds knowledge base from ADO Work Items + GitHub PRs."""

    def __init__(self, output_dir, ado_client, gh_client, dry_run=False):
        self.output_dir = output_dir
        self.ado = ado_client
        self.gh = gh_client
        self.dry_run = dry_run

    def _query_segmented(self, from_date, to_date):
        """Query ADO with segmentation to handle 20k WIQL limit.
        Segments by work item type and 6-month periods if needed."""
        all_ids = set()
        wi_types = ["Bug", "User Story"]

        for wtype in wi_types:
            wiql = (
                f"SELECT [System.Id] "
                f"FROM WorkItems "
                f"WHERE [System.TeamProject] = '{ADO_PROJECT}' "
                f"AND [System.WorkItemType] = '{wtype}' "
                f"AND [System.State] = 'Closed' "
                f"AND [System.ChangedDate] >= '{from_date}' "
                f"AND [System.ChangedDate] <= '{to_date}' "
                f"ORDER BY [System.ChangedDate] DESC"
            )
            log(f"  Querying {wtype} (Closed)...")
            ids = self.ado.wiql_query(wiql)

            if ids is None:
                # Exceeded 20k limit - segment by 6-month periods
                log(f"    Segmenting {wtype} by half-year...")
                ids = self._query_by_periods(wtype, "Closed", from_date, to_date)

            log(f"    {wtype} Closed: {len(ids)}")
            all_ids.update(ids)

            # Also check Resolved state
            wiql_res = wiql.replace("= 'Closed'", "= 'Resolved'")
            log(f"  Querying {wtype} (Resolved)...")
            ids_res = self.ado.wiql_query(wiql_res)
            if ids_res is None:
                ids_res = self._query_by_periods(wtype, "Resolved", from_date, to_date)
            if ids_res:
                log(f"    {wtype} Resolved: {len(ids_res)}")
                all_ids.update(ids_res)

        return sorted(all_ids)

    def _query_by_periods(self, wtype, state, from_date, to_date):
        """Split query into 6-month periods to stay under 20k limit."""
        ids = set()
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)

        current = start
        while current < end:
            # 6-month chunk
            next_date = date(
                current.year + (1 if current.month > 6 else 0),
                (current.month + 6 - 1) % 12 + 1,
                1
            )
            if next_date > end:
                next_date = end

            wiql = (
                f"SELECT [System.Id] "
                f"FROM WorkItems "
                f"WHERE [System.TeamProject] = '{ADO_PROJECT}' "
                f"AND [System.WorkItemType] = '{wtype}' "
                f"AND [System.State] = '{state}' "
                f"AND [System.ChangedDate] >= '{current.isoformat()}' "
                f"AND [System.ChangedDate] <= '{next_date.isoformat()}' "
                f"ORDER BY [System.ChangedDate] DESC"
            )
            chunk_ids = self.ado.wiql_query(wiql)
            if chunk_ids is None:
                log(f"      WARNING: Period {current} to {next_date} still exceeds limit, skipping")
                chunk_ids = []
            log(f"      {current} to {next_date}: {len(chunk_ids)}")
            ids.update(chunk_ids)
            current = next_date

        return list(ids)

    def build(self, from_date, to_date):
        """Full build: query ADO, filter Git WIs, fetch PRs, generate output."""

        # Step 1: WIQL query (segmented to handle 20k limit)
        log(f"\n[1/5] Querying ADO for WIs ({from_date} to {to_date})...")
        wi_ids = self._query_segmented(from_date, to_date)
        log(f"  Total unique WIs: {len(wi_ids)}")

        if not wi_ids:
            log("  No Work Items found. Exiting.")
            return

        # Step 2: Lightweight scan for Git links (relations only)
        log(f"\n[2/6] Scanning WI relations for Git links (batches of {BATCH_SIZE})...")
        all_wis_lite = self.ado.fetch_workitems_relations(wi_ids)
        log(f"  Scanned {len(all_wis_lite)} Work Items")

        # Step 3: Filter WIs with Git links
        log("\n[3/6] Filtering WIs with Git/GitHub links...")
        git_wi_ids = []
        git_infos = {}
        for wi in all_wis_lite:
            git_info = self._extract_git_info(wi)
            if git_info["has_git"]:
                git_wi_ids.append(wi["id"])
                git_infos[wi["id"]] = git_info
        log(f"  {len(git_wi_ids)} WIs have Git links (of {len(all_wis_lite)} total)")
        del all_wis_lite  # Free memory

        if not git_wi_ids:
            log("  No WIs with Git links found. Exiting.")
            return

        if self.dry_run:
            self._print_dry_run_lite(git_wi_ids, git_infos, len(all_wis_lite) if 'all_wis_lite' in dir() else len(wi_ids))
            return

        # Step 4: Fetch full details only for WIs with Git links
        log(f"\n[4/6] Fetching full details for {len(git_wi_ids)} WIs with Git links...")
        git_wis_full = self.ado.fetch_workitems_full(git_wi_ids)
        log(f"  Fetched {len(git_wis_full)} Work Items with full details")

        git_wis = []
        for wi in git_wis_full:
            git_info = git_infos.get(wi["id"], self._extract_git_info(wi))
            git_wis.append((wi, git_info))
        del git_wis_full  # Free memory

        # Step 5: Fetch GitHub PR details (optional)
        entries = []
        if self.gh:
            # Collect all unique PR numbers and prefetch in batch
            all_pr_nums = set()
            for wi, git_info in git_wis:
                all_pr_nums.update(git_info.get("pr_numbers", []))
            log(f"\n[5/6] Fetching {len(all_pr_nums)} unique GitHub PRs (batch GraphQL)...")
            t0 = time.time()
            self.gh.prefetch_prs(list(all_pr_nums))
            log(f"  Prefetch done in {(time.time()-t0)/60:.1f}min, cache size: {len(self.gh._cache)}")

            # Now build entries using cached data
            log(f"  Building entries for {len(git_wis)} WIs...")
            prs_found = 0
            prs_missed = 0
            for idx, (wi, git_info) in enumerate(git_wis):
                pr_numbers = git_info.get("pr_numbers", [])
                pr_data_list = []
                for pr_num in pr_numbers:
                    pr_data = self.gh.get_pr(pr_num)
                    if pr_data:
                        pr_data_list.append(pr_data)
                        prs_found += 1
                    else:
                        prs_missed += 1
                entry = self._build_entry(wi, git_info, pr_data_list)
                entries.append(entry)

            log(f"  PRs found: {prs_found}, missed: {prs_missed}")
        else:
            log(f"\n[5/6] Skipping GitHub PR details (--skip-github)...")
            for wi, git_info in git_wis:
                entry = self._build_entry(wi, git_info, [])
                entries.append(entry)

        # Step 6: Generate output
        log(f"\n[6/6] Generating knowledge base ({len(entries)} entries)...")
        all_wis_count = len(wi_ids) if 'wi_ids' in dir() else len(git_wis)
        self._save(entries, from_date, to_date, all_wis_count)
        log(f"\n  Done! Output: {self.output_dir}")

    def build_incremental(self):
        """Incremental update since last run."""
        meta_path = os.path.join(self.output_dir, METADATA_FILE)
        if not os.path.exists(meta_path):
            log("No previous metadata found. Use --rebuild first.")
            return
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        last_run = meta.get("last_run", "2024-01-01T00:00:00Z")[:10]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log(f"Incremental update: {last_run} to {today}")
        self.build(from_date=last_run, to_date=today)

    def print_stats(self):
        """Print statistics from existing metadata."""
        meta_path = os.path.join(self.output_dir, METADATA_FILE)
        if not os.path.exists(meta_path):
            log("No metadata found. Use --rebuild first.")
            return
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        log(f"\n=== ADO Knowledge Base Statistics ===")
        log(f"Export date: {meta.get('export_date', 'N/A')}")
        qr = meta.get("query_range", {})
        log(f"Query range: {qr.get('from', '?')} to {qr.get('to', '?')}")
        log(f"Total queried: {meta.get('total_queried', 0)}")
        log(f"Total with Git: {meta.get('total_with_git', 0)}")
        log(f"\nBy Type:")
        for t, c in meta.get("by_type", {}).items():
            log(f"  {t}: {c}")
        log(f"\nBy Vertical:")
        for v, info in sorted(meta.get("by_vertical", {}).items()):
            count = info if isinstance(info, int) else info.get("count", 0)
            log(f"  {v}: {count}")
        log()

    # --- Internal methods ---

    def _extract_git_info(self, wi):
        """Extract Git artifact info from WI relations."""
        relations = wi.get("relations", []) or []
        commits = []
        pr_numbers = []
        branches = []
        has_git = False

        for rel in relations:
            url = rel.get("url", "")
            name = rel.get("attributes", {}).get("name", "")

            # ADO Git artifacts: vstfs:///Git/...
            if "vstfs:///Git/Commit/" in url:
                has_git = True
                commits.append({"url": url, "name": name})
            elif "vstfs:///Git/PullRequestId/" in url:
                has_git = True
                match = re.search(r"%2F(\d+)$", url)
                if match:
                    pr_num = int(match.group(1))
                    if pr_num not in pr_numbers:
                        pr_numbers.append(pr_num)
            elif "vstfs:///Git/Ref/" in url:
                has_git = True
                branches.append({"url": url, "name": name})

            # GitHub artifacts: vstfs:///GitHub/...
            elif "vstfs:///GitHub/PullRequest/" in url:
                has_git = True
                match = re.search(r"%2[Ff](\d+)$", url)
                if match:
                    pr_num = int(match.group(1))
                    if pr_num not in pr_numbers:
                        pr_numbers.append(pr_num)
            elif "vstfs:///GitHub/Commit/" in url:
                has_git = True
                commits.append({"url": url, "name": name})

            # Hyperlinks to GitHub PRs
            elif rel.get("rel") == "Hyperlink":
                gh_match = re.search(r"github\.com/[^/]+/[^/]+/pull/(\d+)", url)
                if gh_match:
                    has_git = True
                    pr_num = int(gh_match.group(1))
                    if pr_num not in pr_numbers:
                        pr_numbers.append(pr_num)

        return {
            "has_git": has_git,
            "commits": commits,
            "pr_numbers": pr_numbers,
            "branches": branches,
            "commit_count": len(commits),
        }

    def _classify(self, wi):
        r"""Classify WI into vertical/module from Area Path.

        Area Path patterns:
          ...\TAX ONE\Suporte\{vertical}\{module}
          ...\TAX ONE\{vertical}\{module}
          ...\TAX ONE\{vertical} OS
        """
        fields = wi.get("fields", {})
        area_path = fields.get("System.AreaPath", "")
        parts = [p.strip() for p in area_path.split("\\")]

        vertical = "outros"
        module = "geral"

        # Find the anchor: "Suporte" or "TAX ONE"
        anchor_idx = None
        for i, p in enumerate(parts):
            low = p.lower()
            if low == "suporte":
                anchor_idx = i
                break
            elif low == "tax one":
                anchor_idx = i

        if anchor_idx is not None:
            # Get the next segment after anchor
            remaining = parts[anchor_idx + 1:]
            # Skip "Suporte" if it follows TAX ONE
            if remaining and remaining[0].lower() == "suporte":
                remaining = remaining[1:]

            if remaining:
                raw_vert = remaining[0].lower()
                # Handle "Federal OS", "Estadual OS" etc.
                raw_vert = raw_vert.replace(" os", "").strip()
                vertical = VERTICAL_MAP.get(raw_vert, raw_vert)
            if len(remaining) > 1:
                module = remaining[1]

        return vertical, module

    def _build_entry(self, wi, git_info, pr_data_list):
        """Build a knowledge entry dict from WI + Git info + PR data."""
        fields = wi.get("fields", {})
        vertical, module = self._classify(wi)

        # Collect files changed from all PRs
        all_files = []
        total_additions = 0
        total_deletions = 0
        for pr_data in pr_data_list:
            for f in pr_data.get("files", []):
                all_files.append({
                    "path": f.get("path", ""),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                })
            total_additions += pr_data.get("additions", 0)
            total_deletions += pr_data.get("deletions", 0)

        tags_str = fields.get("System.Tags", "")
        tags = [t.strip() for t in tags_str.split(";") if t.strip()] if tags_str else []

        resolved_date = fields.get("Microsoft.VSTS.Common.ResolvedDate", "")
        closed_date = fields.get("Microsoft.VSTS.Common.ClosedDate", "")
        changed_date = fields.get("System.ChangedDate", "")
        date = resolved_date or closed_date or changed_date
        if date:
            date = date[:10]

        return {
            "id": wi["id"],
            "title": fields.get("System.Title", ""),
            "type": fields.get("System.WorkItemType", ""),
            "state": fields.get("System.State", ""),
            "date": date,
            "vertical": vertical,
            "module": module,
            "area_path": fields.get("System.AreaPath", ""),
            "iteration_path": fields.get("System.IterationPath", ""),
            "tags": tags,
            "description": strip_html(fields.get("System.Description", "")),
            "repro_steps": strip_html(fields.get("Microsoft.VSTS.TCM.ReproSteps", "")),
            "root_cause": strip_html(fields.get("Custom.DescriptionofRootCause", "")),
            "solution": strip_html(fields.get("Custom.Solutions", "")),
            "release_notes": strip_html(fields.get("Custom.DescriptionofReleaseNotes", "")),
            "pr_numbers": git_info["pr_numbers"],
            "commit_count": git_info["commit_count"],
            "files_changed": all_files,
            "total_additions": total_additions,
            "total_deletions": total_deletions,
            "pr_details": [
                {
                    "number": pr.get("number"),
                    "title": pr.get("title", ""),
                    "body": (pr.get("body", "") or "")[:500],
                    "merged_at": pr.get("mergedAt", ""),
                    "state": pr.get("state", ""),
                }
                for pr in pr_data_list
            ],
        }

    def _generate_markdown(self, entry):
        """Generate markdown content for a single WI entry."""
        files_list = entry.get("files_changed", [])
        file_paths = [f["path"] for f in files_list]

        # YAML frontmatter
        lines = ["---"]
        lines.append(f"work_item: {entry['id']}")
        lines.append(f'title: "{entry["title"]}"')
        lines.append(f"type: {entry['type']}")
        lines.append(f"state: {entry['state']}")
        lines.append(f"date: \"{entry['date']}\"")
        lines.append(f"vertical: {entry['vertical']}")
        lines.append(f"module: {entry['module']}")
        if entry["pr_numbers"]:
            lines.append(f"pr: {entry['pr_numbers']}")
        lines.append(f"files_changed: {len(files_list)}")
        lines.append(f"additions: {entry['total_additions']}")
        lines.append(f"deletions: {entry['total_deletions']}")
        if entry["tags"]:
            tags_str = json.dumps(entry["tags"], ensure_ascii=False)
            lines.append(f"tags: {tags_str}")
        lines.append("---")
        lines.append("")

        # Title
        lines.append(f"# {entry['title']}")
        lines.append("")

        # Description
        if entry["description"]:
            lines.append("## Descricao")
            lines.append("")
            lines.append(entry["description"][:2000])
            lines.append("")

        # Repro Steps
        if entry["repro_steps"]:
            lines.append("## Passos de Reproducao")
            lines.append("")
            lines.append(entry["repro_steps"][:2000])
            lines.append("")

        # Root Cause
        if entry["root_cause"]:
            lines.append("## Root Cause")
            lines.append("")
            lines.append(entry["root_cause"][:2000])
            lines.append("")

        # Solution
        if entry["solution"]:
            lines.append("## Solucao")
            lines.append("")
            lines.append(entry["solution"][:2000])
            lines.append("")

        # Release Notes
        if entry["release_notes"]:
            lines.append("## Release Notes")
            lines.append("")
            lines.append(entry["release_notes"][:1000])
            lines.append("")

        # PR Details
        if entry["pr_details"]:
            lines.append("## Pull Requests")
            lines.append("")
            for pr in entry["pr_details"]:
                status = pr.get("state", "")
                merged = pr.get("merged_at", "")
                lines.append(f"### PR #{pr['number']}: {pr['title']}")
                lines.append(f"- Status: {status}")
                if merged:
                    lines.append(f"- Merged: {merged[:10]}")
                if pr.get("body"):
                    lines.append(f"- Descricao: {pr['body'][:500]}")
                lines.append("")

        # Files Changed
        if files_list:
            lines.append("## Arquivos Alterados")
            lines.append("")
            lines.append("| Arquivo | +Linhas | -Linhas |")
            lines.append("|---------|---------|---------|")
            for f in files_list[:50]:  # Limit to 50 files
                lines.append(f"| `{f['path']}` | {f['additions']} | {f['deletions']} |")
            if len(files_list) > 50:
                lines.append(f"| ... e mais {len(files_list) - 50} arquivos | | |")
            lines.append("")

        # Tags
        if entry["tags"]:
            lines.append("## Tags")
            lines.append("")
            lines.append(", ".join(f"`{t}`" for t in entry["tags"]))
            lines.append("")

        return "\n".join(lines)

    def _save(self, entries, from_date, to_date, total_queried):
        """Save markdown files and metadata JSON."""
        os.makedirs(self.output_dir, exist_ok=True)

        # Ensure vertical directories exist
        verticals_seen = set()
        for entry in entries:
            verticals_seen.add(entry["vertical"])
        for v in verticals_seen:
            os.makedirs(os.path.join(self.output_dir, v), exist_ok=True)

        # Write individual markdown files
        for entry in entries:
            filepath = os.path.join(self.output_dir, entry["vertical"], f"{entry['id']}.md")
            md_content = self._generate_markdown(entry)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(md_content)

        # Build metadata
        by_vertical = {}
        by_type = {}
        meta_entries = []

        for entry in entries:
            v = entry["vertical"]
            if v not in by_vertical:
                by_vertical[v] = {"count": 0, "modules": set()}
            by_vertical[v]["count"] += 1
            by_vertical[v]["modules"].add(entry["module"])

            t = entry["type"]
            by_type[t] = by_type.get(t, 0) + 1

            meta_entries.append({
                "id": entry["id"],
                "title": entry["title"],
                "type": entry["type"],
                "state": entry["state"],
                "vertical": entry["vertical"],
                "module": entry["module"],
                "date": entry["date"],
                "tags": entry["tags"],
                "has_root_cause": bool(entry["root_cause"]),
                "has_solution": bool(entry["solution"]),
                "pr_numbers": entry["pr_numbers"],
                "files_changed_count": len(entry["files_changed"]),
                "additions": entry["total_additions"],
                "deletions": entry["total_deletions"],
                "file": f"{entry['vertical']}/{entry['id']}.md",
            })

        # Convert sets to sorted lists for JSON
        for v in by_vertical:
            by_vertical[v]["modules"] = sorted(by_vertical[v]["modules"])

        metadata = {
            "export_date": datetime.now(timezone.utc).isoformat(),
            "query_range": {"from": from_date, "to": to_date},
            "total_queried": total_queried,
            "total_with_git": len(entries),
            "by_type": by_type,
            "by_vertical": by_vertical,
            "entries": meta_entries,
            "last_run": datetime.now(timezone.utc).isoformat(),
        }

        # Merge with existing metadata on incremental update
        meta_path = os.path.join(self.output_dir, METADATA_FILE)
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing_ids = {e["id"] for e in existing.get("entries", [])}
            new_ids = {e["id"] for e in meta_entries}
            # Keep existing entries not in new batch, add all new
            merged = [e for e in existing.get("entries", []) if e["id"] not in new_ids]
            merged.extend(meta_entries)
            metadata["entries"] = merged
            metadata["total_with_git"] = len(merged)
            # Recalculate by_vertical and by_type
            metadata["by_type"] = {}
            metadata["by_vertical"] = {}
            for e in merged:
                t = e["type"]
                metadata["by_type"][t] = metadata["by_type"].get(t, 0) + 1
                v = e["vertical"]
                if v not in metadata["by_vertical"]:
                    metadata["by_vertical"][v] = {"count": 0, "modules": set()}
                metadata["by_vertical"][v]["count"] += 1
                metadata["by_vertical"][v]["modules"].add(e["module"])
            for v in metadata["by_vertical"]:
                metadata["by_vertical"][v]["modules"] = sorted(metadata["by_vertical"][v]["modules"])

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        log(f"\n  Metadata: {meta_path}")
        log(f"  Files written: {len(entries)}")
        log(f"  Total in index: {len(metadata['entries'])}")

    def _print_dry_run_lite(self, git_wi_ids, git_infos, total_scanned):
        """Print summary for dry-run mode (lightweight, no full details)."""
        log(f"\n=== DRY RUN SUMMARY ===\n")
        log(f"  Total WIs scanned: {total_scanned}")
        log(f"  WIs with Git links: {len(git_wi_ids)}")
        log(f"  Git link rate: {len(git_wi_ids)*100/total_scanned:.1f}%")

        # Count PRs
        total_prs = sum(len(info.get("pr_numbers", [])) for info in git_infos.values())
        total_commits = sum(info.get("commit_count", 0) for info in git_infos.values())
        log(f"  Total unique PRs referenced: {total_prs}")
        log(f"  Total commits referenced: {total_commits}")

        log(f"\nSample WI IDs with Git (first 20): {git_wi_ids[:20]}")
        log(f"\nNext step: run without --dry-run to fetch full details and generate knowledge base\n")

    def _print_dry_run(self, git_wis):
        """Print summary for dry-run mode (full details available)."""
        log(f"\n=== DRY RUN - Would process {len(git_wis)} WIs ===\n")

        by_vertical = {}
        by_type = {}
        for wi, git_info in git_wis:
            fields = wi.get("fields", {})
            v, m = self._classify(wi)
            t = fields.get("System.WorkItemType", "?")
            by_vertical[v] = by_vertical.get(v, 0) + 1
            by_type[t] = by_type.get(t, 0) + 1

        log("By Type:")
        for t, c in sorted(by_type.items()):
            log(f"  {t}: {c}")

        log("\nBy Vertical:")
        for v, c in sorted(by_vertical.items()):
            log(f"  {v}: {c}")

        log("\nSample WIs (first 10):")
        for wi, git_info in git_wis[:10]:
            fields = wi.get("fields", {})
            wi_id = wi["id"]
            title = fields.get("System.Title", "")[:60]
            prs = git_info.get("pr_numbers", [])
            log(f"  #{wi_id}: {title} (PRs: {prs})")
        log()


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(
        description="Build knowledge base from ADO Work Items + GitHub PRs"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--rebuild", action="store_true",
                       help="Full rebuild from --from-date (default: 2024-01-01)")
    group.add_argument("--update", action="store_true",
                       help="Incremental update since last run")
    group.add_argument("--stats", action="store_true",
                       help="Print statistics from existing metadata")

    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only, don't write files")
    parser.add_argument("--skip-github", action="store_true",
                        help="Skip GitHub PR fetch (ADO data only, much faster)")
    parser.add_argument("--from-date", default="2024-01-01",
                        help="Start date for --rebuild (YYYY-MM-DD, default: 2024-01-01)")
    parser.add_argument("--to-date", default=None,
                        help="End date (YYYY-MM-DD, default: today)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"Output directory (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--no-cache", action="store_true",
                        help="Force re-fetch all PRs (ignore disk cache)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Parallel workers for GitHub fetch (default: {DEFAULT_WORKERS})")

    args = parser.parse_args()

    if args.stats:
        builder = KnowledgeBuilder(args.output, None, None)
        builder.print_stats()
        return

    ado = ADOClient(workers=ADO_WORKERS)
    gh = None if args.skip_github else GitHubClient(
        workers=args.workers,
        cache_dir=args.output,
        use_cache=not args.no_cache,
    )
    builder = KnowledgeBuilder(args.output, ado, gh, dry_run=args.dry_run)

    if args.rebuild:
        to_date = args.to_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        builder.build(from_date=args.from_date, to_date=to_date)
    elif args.update:
        builder.build_incremental()


if __name__ == "__main__":
    main()
