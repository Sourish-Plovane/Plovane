"""Shared fixtures: a small library and a wired-up template recommender."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ml.ranking.features import UsageIndex
from ml.retrieval.embedding import HashingEmbeddingModel, embed_entries_inplace
from ml.retrieval.keyword import InMemoryBM25Index
from ml.retrieval.service import RetrievalService
from ml.retrieval.vector import InMemoryVectorIndex
from ml.schemas import Entry, EntryType, Visibility
from ml.templates.recommender import TemplateRecommender

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def _entry(**kwargs) -> Entry:
    defaults = {
        "category": "scaffolding",
        "language": "javascript",
        "license": "MIT",
        "impressions": 200,
        "accepts": 120,
        "usage_count": 100,
        "mutation_score": 0.7,
        "last_retest_at": NOW - timedelta(days=5),
    }
    return Entry(**{**defaults, **kwargs})


@pytest.fixture
def library() -> list[Entry]:
    """Templates, bundles and helpers, with three near-identical React starters.

    The duplicates are the point: they are what MMR has to break up, and a
    library without them cannot show whether it does.
    """
    return [
        _entry(
            id="tpl_react_1",
            type=EntryType.TEMPLATE,
            name="react_vite_starter",
            description="React single-page app scaffold with Vite, routing and ESLint",
            framework="react",
            code="import React from 'react'\nimport { createRoot } from 'react-dom/client'\n" * 40,
            dependencies=("react", "vite", "eslint"),
            tags=("react", "vite", "spa"),
            members=9,
        ),
        _entry(
            id="tpl_react_2",
            type=EntryType.TEMPLATE,
            name="react_vite_starter_prettier",
            description="React single-page app scaffold with Vite, routing and Prettier",
            framework="react",
            code="import React from 'react'\nimport { createRoot } from 'react-dom/client'\n" * 40,
            dependencies=("react", "vite", "prettier"),
            tags=("react", "vite", "spa"),
            members=9,
            usage_count=60,
        ),
        _entry(
            id="tpl_react_3",
            type=EntryType.TEMPLATE,
            name="react_vite_starter_ts",
            description="React single-page app scaffold with Vite, routing and TypeScript",
            framework="react",
            code="import React from 'react'\nimport { createRoot } from 'react-dom/client'\n" * 40,
            dependencies=("react", "vite", "typescript"),
            tags=("react", "vite", "spa"),
            members=10,
            usage_count=55,
        ),
        _entry(
            id="tpl_vue",
            type=EntryType.TEMPLATE,
            name="vue_spa_starter",
            description="Vue single-page app scaffold with Vite and Pinia",
            framework="vue",
            code="import { createApp } from 'vue'\n" * 30,
            dependencies=("vue", "vite", "pinia"),
            tags=("vue", "spa"),
            members=8,
        ),
        _entry(
            id="tpl_fastapi",
            type=EntryType.TEMPLATE,
            name="fastapi_service",
            description="FastAPI service scaffold with auth, migrations and test harness",
            language="python",
            framework="fastapi",
            code="from fastapi import FastAPI, Depends\n" * 30,
            dependencies=("fastapi", "sqlalchemy", "alembic"),
            tags=("api", "auth", "service"),
            members=14,
        ),
        _entry(
            id="bd_react_testing",
            type=EntryType.BUNDLE,
            name="react_testing_bundle",
            description="Vitest, Testing Library and CI config that work together for React",
            framework="react",
            code="import { render } from '@testing-library/react'\n" * 20,
            dependencies=("vitest", "@testing-library/react"),
            tags=("react", "testing"),
            members=4,
        ),
        _entry(
            id="tpl_private",
            type=EntryType.TEMPLATE,
            name="internal_react_shell",
            description="Internal React app shell with the house design system wired in",
            framework="react",
            code="import { Shell } from '@internal/design'\n" * 20,
            visibility=Visibility.PRIVATE,
            owner_id="user_sourish",
            members=11,
        ),
        _entry(
            id="tpl_stale_gpl",
            type=EntryType.TEMPLATE,
            name="legacy_react_boilerplate",
            description="Older React boilerplate using class components and Webpack",
            framework="react",
            code="var React = require('react')\n" * 20,
            license="GPL-3.0",
            last_retest_at=NOW - timedelta(days=400),
            members=12,
        ),
        # Helpers must never surface in template results.
        _entry(
            id="hf_react_hook",
            type=EntryType.HELPER,
            name="useLocalStorage",
            description="React hook that persists state to localStorage",
            framework="react",
            signature="(key, initial) -> [value, setValue]",
            code="export function useLocalStorage(key, initial) {}",
        ),
        _entry(
            id="hf_split",
            type=EntryType.HELPER,
            name="stratified_split",
            description="Splits a dataframe while preserving label distribution",
            language="python",
            signature="(df, label_col, test_size=0.2) -> (train, test)",
        ),
    ]


@pytest.fixture
def embedding_model() -> HashingEmbeddingModel:
    return HashingEmbeddingModel(dimension=128, seed=7)


@pytest.fixture
def recommender(library, embedding_model) -> TemplateRecommender:
    embed_entries_inplace(embedding_model, library)
    store = {entry.id: entry for entry in library}
    retrieval = RetrievalService(
        keyword_index=InMemoryBM25Index(library),
        vector_index=InMemoryVectorIndex(library),
        embedding_model=embedding_model,
        entry_store=store,
    )
    return TemplateRecommender(
        retrieval=retrieval,
        usage_index=UsageIndex(),
        global_acceptance_rate=0.45,
        # The untrained path has no calibrator, so its scores are not
        # probabilities. A cutoff on them would be arbitrary; these tests are
        # about ordering and selection, so it is set to admit everything and
        # the cutoff gets its own test with a fitted calibrator.
        cutoff=0.0,
    )
