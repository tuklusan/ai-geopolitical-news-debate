# ============================================================================
# Copyright (c) 2026 Supratim Sanyal of SANYALnet Labs.
# Proprietary rights reserved except as expressly licensed herein.
#
# LIVE NEWS DEBATE WALL
# This file is governed by the SANYALnet Labs Non-Commercial License in the
# root LICENSE file. Non-Commercial use is permitted; Commercial Use and use
# for AI/ML model training are prohibited unless separately authorized.
#
# Attribution is required: "Based on original work by Supratim Sanyal of
# SANYALnet Labs." See LICENSE for full terms, warranty disclaimer, termination,
# patent, trademark, and governing-law provisions.
# ============================================================================

"""
Shared test fixtures.

The application reads its settings from ``os.environ``, and ``load_dotenv``
deliberately does not overwrite a variable that is already set. A test that
loads a dotenv file therefore leaves those values in the process environment
and silently changes the meaning of every test that runs after it. Snapshot
and restore the environment around each test so ordering cannot matter.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_environment():
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
