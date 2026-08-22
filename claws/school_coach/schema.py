from __future__ import annotations

import sqlite3


def ensure_school_coach_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS school_coach_relationships (
            id TEXT PRIMARY KEY,
            person_name TEXT NOT NULL,
            person_name_key TEXT NOT NULL,
            role TEXT,
            child TEXT,
            school TEXT,
            desired_state TEXT,
            status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS school_coach_decision_records (
            id TEXT PRIMARY KEY,
            relationship_id TEXT NOT NULL REFERENCES school_coach_relationships(id),
            trigger_kind TEXT NOT NULL,
            trigger_ref TEXT NOT NULL,
            observed_summary TEXT NOT NULL,
            conclusion_summary TEXT,
            decision_summary TEXT,
            confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            model TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS school_coach_interactions (
            id TEXT PRIMARY KEY,
            relationship_id TEXT NOT NULL REFERENCES school_coach_relationships(id),
            decision_id TEXT REFERENCES school_coach_decision_records(id),
            occurred_at TEXT,
            channel TEXT,
            summary TEXT NOT NULL,
            outcome TEXT,
            provenance_kind TEXT NOT NULL,
            provenance_ref TEXT NOT NULL,
            reported_by TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS school_coach_observations (
            id TEXT PRIMARY KEY,
            relationship_id TEXT NOT NULL REFERENCES school_coach_relationships(id),
            interaction_id TEXT REFERENCES school_coach_interactions(id),
            decision_id TEXT REFERENCES school_coach_decision_records(id),
            statement TEXT NOT NULL,
            observed_at TEXT,
            provenance_kind TEXT NOT NULL CHECK (
                provenance_kind IN (
                    'user_report', 'user_correction', 'interaction',
                    'school_markdown', 'journal_markdown'
                )
            ),
            provenance_ref TEXT NOT NULL,
            reported_by TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS school_coach_beliefs (
            id TEXT PRIMARY KEY,
            relationship_id TEXT NOT NULL REFERENCES school_coach_relationships(id),
            topic_key TEXT NOT NULL,
            version INTEGER NOT NULL CHECK (version > 0),
            previous_belief_id TEXT REFERENCES school_coach_beliefs(id),
            statement TEXT NOT NULL,
            confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'retracted')),
            change_reason TEXT NOT NULL,
            decision_id TEXT REFERENCES school_coach_decision_records(id),
            created_at TEXT NOT NULL,
            UNIQUE (relationship_id, topic_key, version)
        );

        CREATE TABLE IF NOT EXISTS school_coach_belief_evidence (
            belief_id TEXT NOT NULL REFERENCES school_coach_beliefs(id),
            observation_id TEXT NOT NULL REFERENCES school_coach_observations(id),
            stance TEXT NOT NULL CHECK (stance IN ('supports', 'contradicts')),
            PRIMARY KEY (belief_id, observation_id)
        );

        CREATE TABLE IF NOT EXISTS school_coach_strategies (
            id TEXT PRIMARY KEY,
            relationship_id TEXT NOT NULL REFERENCES school_coach_relationships(id),
            version INTEGER NOT NULL CHECK (version > 0),
            previous_strategy_id TEXT REFERENCES school_coach_strategies(id),
            goal TEXT,
            plan TEXT NOT NULL,
            rationale TEXT NOT NULL,
            confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'retired')),
            change_reason TEXT NOT NULL,
            decision_id TEXT REFERENCES school_coach_decision_records(id),
            created_at TEXT NOT NULL,
            UNIQUE (relationship_id, version)
        );

        CREATE TABLE IF NOT EXISTS school_coach_strategy_beliefs (
            strategy_id TEXT NOT NULL REFERENCES school_coach_strategies(id),
            belief_id TEXT NOT NULL REFERENCES school_coach_beliefs(id),
            PRIMARY KEY (strategy_id, belief_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_school_coach_active_belief
        ON school_coach_beliefs(relationship_id, topic_key)
        WHERE status = 'active';

        CREATE UNIQUE INDEX IF NOT EXISTS idx_school_coach_active_strategy
        ON school_coach_strategies(relationship_id)
        WHERE status = 'active';

        CREATE INDEX IF NOT EXISTS idx_school_coach_relationship_name
        ON school_coach_relationships(person_name_key, status);

        CREATE INDEX IF NOT EXISTS idx_school_coach_observation_relationship
        ON school_coach_observations(relationship_id, created_at);
        """
    )
