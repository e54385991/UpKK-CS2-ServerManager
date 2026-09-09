"""Round-robin discovery with a task-local marketplace index and search deadline."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable

from modules.plugin_ai import DiscoveryProgress, ImportOptions, repository_url
from services.plugins import ai_discovery as policy
from services.plugins import ai_import_store as store
from services.plugins.ai_candidate_screening import BATCH_SIZE, Repository
from services.plugins.ai_search_plan import planned_searches
from services.plugins.github_ai_client import GitHubAIClient, GitHubImportError

type PlanSearches = Callable[[list[str]], Awaitable[dict[str, list[str]]]]
type Progress = Callable[[str, str], Awaitable[None]]


class CandidateDiscovery:
    def __init__(
        self, options: ImportOptions, client: GitHubAIClient, plan: PlanSearches, progress: Progress
    ) -> None:
        self.options, self.client, self.plan, self.progress = options, client, plan, progress
        self.known: dict[str, int] | None = None
        self.rows: dict[str, Repository] = {}
        self.seen: set[str] = set()
        self.existing: set[str] = set()
        self.stats = DiscoveryProgress()
        self.search_remaining = float(max(5, min(90, options.minutes * 20)))
        self.search_exhausted = False

    async def refresh(self) -> None:
        self.known = dict(await store.existing_repositories())

    async def ensure_index(self) -> dict[str, int]:
        if self.known is None:
            await self.refresh()
        return self.known if self.known is not None else {}

    def mark_existing(self, url: str) -> bool:
        if self.known is None or url not in self.known:
            return False
        if url in self.seen and url not in self.existing:
            self.existing.add(url)
            self.stats.existing += 1
        return True

    def collect(self, row: Repository) -> str | None:
        try:
            url = repository_url(str(row["html_url"]))
        except ValueError, KeyError:
            return None
        if url in self.seen:
            return None
        self.seen.add(url)
        self.stats.discovered += 1
        if self.mark_existing(url):
            return None
        # A search result contains repository metadata, so do not fetch it again.
        self.rows[url] = {**row, "html_url": url}
        return url

    async def search_page(self, term: str, page: int) -> list[Repository]:
        await self.progress("searching", f"Searching GitHub: {term} (page {page})")
        if self.search_remaining <= 0:
            self.search_exhausted = True
            return []
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.search_remaining):
                return await self.client.search(self.options, term, page)
        except TimeoutError:
            self.search_exhausted = True
            await self.progress(
                "searching", "Search budget reached; processing collected candidates"
            )
            return []
        except GitHubImportError as exc:
            if exc.status != 422:
                raise
            await self.progress("searching", "GitHub rejected one query; continuing discovery")
            return []
        finally:
            # Only GitHub search time is charged, never screening or installation analysis.
            self.search_remaining -= time.monotonic() - started

    async def rounds(self, searches: list[list[str]]) -> AsyncGenerator[list[str], None]:
        for index in range(max(map(len, searches), default=0)):
            exhausted: set[int] = set()
            for page in range(1, policy.SEARCH_PAGES + 1):
                urls: list[str] = []
                for framework, terms in enumerate(searches):
                    if framework in exhausted or index >= len(terms) or self.search_exhausted:
                        continue
                    batch = await self.search_page(terms[index], page)
                    if len(batch) < 50:
                        exhausted.add(framework)
                    urls.extend(url for row in batch if (url := self.collect(row)) is not None)
                yield sorted(
                    urls,
                    key=lambda url: policy.sort_ranking(self.rows[url], self.options.sort_priority),
                    reverse=True,
                )
                if self.search_exhausted:
                    return

    async def batches(self) -> AsyncGenerator[list[str], None]:
        await self.ensure_index()
        explicit = [
            url
            for value in self.options.repositories
            if (url := self.collect({"html_url": value})) is not None
        ]
        # Explicit URLs have no metadata yet and retain their existing privileged entry path.
        for url in explicit:
            self.rows.pop(url)
        if explicit:
            yield explicit
        frameworks = (
            list(policy.FRAMEWORK_TERMS)
            if self.options.framework == "all"
            else [self.options.framework]
        )
        plans = await self.plan(frameworks) if self.options.expand_search else {}
        searches = [
            planned_searches(key, self.options.keywords, plans.get(key, [])) for key in frameworks
        ]
        async for urls in self.rounds(searches):
            for start in range(0, len(urls), BATCH_SIZE):
                yield urls[start : start + BATCH_SIZE]
            await self.progress("filtering", "Discovery round completed")
