"""Owner-reviewed terminology changes, with atomic clip/history/preset migration."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from contextlib import contextmanager
from uuid import uuid4

from .models import ClipPatch
from .service import Catalog, CatalogError, encode
from .tagging import changed, resolve, revision


class Taxonomy:
    def __init__(self, catalog: Catalog):
        self.catalog, self.db = catalog, catalog.db

    @contextmanager
    def _write(self):
        try:
            with self.db.connection(write=True) as connection:
                yield connection
        except sqlite3.Error as error:
            raise CatalogError(
                "taxonomy_storage_error",
                "Could not save terminology. Changes were rolled back; check disk space and retry",
                503,
            ) from error

    @staticmethod
    def _check(connection: sqlite3.Connection, expected: int) -> None:
        if revision(connection) != expected:
            raise CatalogError(
                "taxonomy_conflict", "Terminology changed. Reload it before saving", 409
            )

    @staticmethod
    def _require(connection: sqlite3.Connection, table: str, item_id: str) -> dict:
        if table not in {"tag_groups", "tag_terms", "tag_presets"}:
            raise ValueError("Unknown terminology table")
        row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise CatalogError("taxonomy_not_found", "This terminology item no longer exists", 404)
        return dict(row)

    @staticmethod
    def _aliases(connection: sqlite3.Connection, tag_id: str) -> list[dict]:
        return [
            {"name": row[0], "protected": bool(row[1])}
            for row in connection.execute(
                "SELECT name,is_legacy FROM tag_names WHERE tag_id=? AND is_canonical=0 "
                "ORDER BY name",
                (tag_id,),
            )
        ]

    def _term(self, connection: sqlite3.Connection, tag_id: str) -> dict:
        term = self._require(connection, "tag_terms", tag_id)
        term["name"] = connection.execute(
            "SELECT name FROM tag_names WHERE tag_id=? AND is_canonical=1", (tag_id,)
        ).fetchone()[0]
        term["aliases"] = self._aliases(connection, tag_id)
        return term

    def _snapshot(self, connection: sqlite3.Connection) -> dict:
        groups = [
            dict(row)
            for row in connection.execute(
                "SELECT id,name,description FROM tag_groups ORDER BY name_key,id"
            )
        ]
        tags = []
        for row in connection.execute(
            """SELECT t.id,t.group_id,t.description,n.name,
                COALESCE(SUM(c.archived=0),0) AS count,
                COALESCE(SUM(c.archived=1),0) AS archived_count
                FROM tag_terms t JOIN tag_names n ON n.tag_id=t.id AND n.is_canonical=1
                LEFT JOIN clip_tags ct ON ct.tag=n.name LEFT JOIN clips c ON c.id=ct.clip_id
                GROUP BY t.id ORDER BY n.name,t.id"""
        ).fetchall():
            term = dict(row)
            term["aliases"] = self._aliases(connection, term["id"])
            tags.append(term)
        presets = []
        for row in connection.execute(
            "SELECT id,name,description FROM tag_presets ORDER BY name_key,id"
        ).fetchall():
            preset = dict(row)
            preset["tag_ids"] = [
                item[0]
                for item in connection.execute(
                    "SELECT tag_id FROM tag_preset_items WHERE preset_id=? ORDER BY tag_id",
                    (preset["id"],),
                )
            ]
            presets.append(preset)
        return {
            "revision": revision(connection),
            "groups": groups,
            "tags": tags,
            "presets": presets,
        }

    def snapshot(self) -> dict:
        with self.db.connection() as connection:
            return self._snapshot(connection)

    def save_group(self, body, group_id: str | None = None) -> dict:
        with self._write() as connection:
            self._check(connection, body.expected_revision)
            before = self._require(connection, "tag_groups", group_id) if group_id else None
            duplicate = connection.execute(
                "SELECT id FROM tag_groups WHERE name_key=?", (body.name.casefold(),)
            ).fetchone()
            if duplicate and duplicate[0] != group_id:
                raise CatalogError("taxonomy_name_conflict", "A group already uses this name", 409)
            group_id = group_id or str(uuid4())
            connection.execute(
                """INSERT INTO tag_groups(id,name,name_key,description) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,name_key=excluded.name_key,
                description=excluded.description""",
                (group_id, body.name, body.name.casefold(), body.description),
            )
            changed(
                connection,
                "group_saved",
                {
                    "before": before,
                    "id": group_id,
                    "name": body.name,
                    "description": body.description,
                },
            )
            return self._snapshot(connection)

    def delete_group(self, group_id: str, expected: int) -> dict:
        with self._write() as connection:
            self._check(connection, expected)
            before = self._require(connection, "tag_groups", group_id)
            terms = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM tag_terms WHERE group_id=? ORDER BY id", (group_id,)
                )
            ]
            connection.execute("DELETE FROM tag_groups WHERE id=?", (group_id,))
            changed(connection, "group_deleted", {"before": before, "ungrouped_tag_ids": terms})
            return self._snapshot(connection)

    def save_term(self, body, tag_id: str | None = None) -> dict:
        with self._write() as connection:
            self._check(connection, body.expected_revision)
            before = self._term(connection, tag_id) if tag_id else None
            group_id = str(body.group_id) if body.group_id else None
            if group_id:
                self._require(connection, "tag_groups", group_id)
            if tag_id is None:
                if resolve(connection, body.name):
                    raise CatalogError(
                        "taxonomy_name_conflict", "That name is already a tag or alias", 409
                    )
                tag_id = str(uuid4())
                connection.execute(
                    "INSERT INTO tag_terms(id,group_id,description) VALUES(?,?,?)",
                    (tag_id, group_id, body.description),
                )
                connection.execute(
                    "INSERT INTO tag_names(name,tag_id,is_canonical) VALUES(?,?,1)",
                    (body.name, tag_id),
                )
            else:
                connection.execute(
                    "UPDATE tag_terms SET group_id=?,description=? WHERE id=?",
                    (group_id, body.description, tag_id),
                )
            changed(
                connection,
                "term_saved",
                {"before": before, "after": self._term(connection, tag_id)},
            )
            return self._snapshot(connection)

    def alias(self, tag_id: str, body, *, remove: bool = False) -> dict:
        with self._write() as connection:
            self._check(connection, body.expected_revision)
            self._term(connection, tag_id)
            existing = connection.execute(
                "SELECT * FROM tag_names WHERE name=?", (body.name,)
            ).fetchone()
            if remove:
                if not existing or existing["tag_id"] != tag_id:
                    raise CatalogError("taxonomy_not_found", "This alias does not exist", 404)
                if existing["is_canonical"] or existing["is_legacy"]:
                    raise CatalogError(
                        "protected_tag_name",
                        "Canonical and historical names cannot be removed",
                        409,
                    )
                connection.execute("DELETE FROM tag_names WHERE name=?", (body.name,))
            else:
                if existing:
                    raise CatalogError(
                        "taxonomy_name_conflict",
                        "This spelling exists. Use a previewed rename/map for an existing tag",
                        409,
                    )
                connection.execute(
                    "INSERT INTO tag_names(name,tag_id,is_canonical) VALUES(?,?,0)",
                    (body.name, tag_id),
                )
            changed(
                connection,
                "alias_removed" if remove else "alias_added",
                {"tag_id": tag_id, "name": body.name},
            )
            return self._snapshot(connection)

    def save_preset(self, body, preset_id: str | None = None) -> dict:
        with self._write() as connection:
            self._check(connection, body.expected_revision)
            before = self._require(connection, "tag_presets", preset_id) if preset_id else None
            duplicate = connection.execute(
                "SELECT id FROM tag_presets WHERE name_key=?", (body.name.casefold(),)
            ).fetchone()
            if duplicate and duplicate[0] != preset_id:
                raise CatalogError("taxonomy_name_conflict", "A preset already uses this name", 409)
            tag_ids = sorted(str(value) for value in body.tag_ids)
            for tag_id in tag_ids:
                self._require(connection, "tag_terms", tag_id)
            preset_id = preset_id or str(uuid4())
            if before:
                before["tag_ids"] = [
                    row[0]
                    for row in connection.execute(
                        "SELECT tag_id FROM tag_preset_items WHERE preset_id=? ORDER BY tag_id",
                        (preset_id,),
                    )
                ]
            connection.execute(
                """INSERT INTO tag_presets(id,name,name_key,description) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,name_key=excluded.name_key,
                description=excluded.description""",
                (preset_id, body.name, body.name.casefold(), body.description),
            )
            connection.execute("DELETE FROM tag_preset_items WHERE preset_id=?", (preset_id,))
            connection.executemany(
                "INSERT INTO tag_preset_items(preset_id,tag_id) VALUES(?,?)",
                [(preset_id, tag_id) for tag_id in tag_ids],
            )
            changed(
                connection,
                "preset_saved",
                {
                    "before": before,
                    "id": preset_id,
                    "name": body.name,
                    "description": body.description,
                    "tag_ids": tag_ids,
                },
            )
            return self._snapshot(connection)

    def delete_preset(self, preset_id: str, expected: int) -> dict:
        with self._write() as connection:
            self._check(connection, expected)
            before = self._require(connection, "tag_presets", preset_id)
            before["tag_ids"] = [
                row[0]
                for row in connection.execute(
                    "SELECT tag_id FROM tag_preset_items WHERE preset_id=? ORDER BY tag_id",
                    (preset_id,),
                )
            ]
            connection.execute("DELETE FROM tag_presets WHERE id=?", (preset_id,))
            changed(connection, "preset_deleted", {"before": before})
            return self._snapshot(connection)

    def apply_preset(self, clip_id: str, preset_id: str, body) -> dict:
        with self._write() as connection:
            self._check(connection, body.expected_revision)
            self._require(connection, "tag_presets", preset_id)
            clip = self.catalog._clip(connection, clip_id)
            if body.version != clip["version"]:
                raise CatalogError(
                    "version_conflict", "This clip changed. Reload before saving", 409
                )
            names = [
                row[0]
                for row in connection.execute(
                    """SELECT n.name FROM tag_preset_items p JOIN tag_names n ON n.tag_id=p.tag_id
                AND n.is_canonical=1 WHERE p.preset_id=?""",
                    (preset_id,),
                )
            ]
            tags = sorted(set(clip["tags"]) | set(names))
            if len(tags) > 40:
                raise CatalogError("preset_tag_limit", "A clip can have at most 40 tags", 422)
            if tags == clip["tags"]:
                return clip
            return self.catalog._patch(
                connection, clip_id, ClipPatch(version=body.version, tags=tags)
            )

    def _plan(self, connection: sqlite3.Connection, tag_id: str, new_name: str) -> dict:
        source = self._term(connection, tag_id)
        if new_name == source["name"]:
            raise CatalogError("unchanged_tag_name", "Choose a different canonical name", 422)
        match = resolve(connection, new_name)
        merge = match is not None and match["id"] != tag_id
        target = self._term(connection, match["id"]) if merge else {**source, "name": new_name}
        affected = []
        for row in connection.execute(
            "SELECT clip_id FROM clip_tags WHERE tag=? ORDER BY clip_id", (source["name"],)
        ).fetchall():
            clip = self.catalog._clip(connection, row[0])
            affected.append(
                {
                    "id": clip["id"],
                    "title": clip["title"],
                    "version": clip["version"],
                    "archived": clip["archived"],
                    "before": clip["tags"],
                    "after": sorted((set(clip["tags"]) - {source["name"]}) | {target["name"]}),
                }
            )
        target_usage = (
            [
                dict(row)
                for row in connection.execute(
                    """SELECT c.id,c.version,c.archived FROM clips c
                    JOIN clip_tags t ON t.clip_id=c.id
            WHERE t.tag=? ORDER BY c.id""",
                    (target["name"],),
                )
            ]
            if merge
            else []
        )
        presets = [
            dict(row)
            for row in connection.execute(
                """SELECT p.id,p.name FROM tag_presets p JOIN tag_preset_items i ON i.preset_id=p.id
            WHERE i.tag_id=? ORDER BY p.id""",
                (tag_id,),
            )
        ]
        population = {item["id"]: item for item in [*target_usage, *affected]}
        plan = {
            "revision": revision(connection),
            "operation": "map" if merge else "rename",
            "source": source,
            "target": target,
            "requested_name": new_name,
            "affected_clips": affected,
            "affected_presets": presets,
            "target_usage": target_usage,
            "active_count_after": sum(not x["archived"] for x in population.values()),
            "archived_count_after": sum(bool(x["archived"]) for x in population.values()),
        }
        plan["preview_token"] = hashlib.sha256(encode(plan).encode("utf-8")).hexdigest()
        return plan

    def preview(self, body) -> dict:
        with self.db.connection() as connection:
            return self._plan(connection, str(body.tag_id), body.new_name)

    def apply_change(self, body) -> dict:
        with self._write() as connection:
            plan = self._plan(connection, str(body.tag_id), body.new_name)
            if not hmac.compare_digest(plan["preview_token"], body.preview_token):
                raise CatalogError(
                    "taxonomy_preview_stale", "Clips or terminology changed. Preview again", 409
                )
            source, target = plan["source"], plan["target"]
            connection.execute(
                "UPDATE tag_names SET is_canonical=0,is_legacy=1 WHERE name=?", (source["name"],)
            )
            if plan["operation"] == "map":
                connection.execute(
                    "UPDATE tag_names SET tag_id=? WHERE tag_id=?", (target["id"], source["id"])
                )
                connection.execute(
                    """INSERT OR IGNORE INTO tag_preset_items(preset_id,tag_id)
                    SELECT preset_id,? FROM tag_preset_items WHERE tag_id=?""",
                    (target["id"], source["id"]),
                )
                connection.execute("DELETE FROM tag_preset_items WHERE tag_id=?", (source["id"],))
                connection.execute("DELETE FROM tag_terms WHERE id=?", (source["id"],))
            else:
                connection.execute(
                    """INSERT INTO tag_names(name,tag_id,is_canonical) VALUES(?,?,1)
                    ON CONFLICT(name) DO UPDATE SET is_canonical=1""",
                    (target["name"], source["id"]),
                )
            for clip in plan["affected_clips"]:
                self.catalog._patch(
                    connection, clip["id"], ClipPatch(version=clip["version"], tags=clip["after"])
                )
            changed(
                connection, "term_mapped" if plan["operation"] == "map" else "term_renamed", plan
            )
            return {"taxonomy": self._snapshot(connection), "result": plan}

    def events(self, limit: int = 30) -> list[dict]:
        with self.db.connection() as connection:
            return [
                {**dict(row), "payload": json.loads(row["payload"])}
                for row in connection.execute(
                    "SELECT * FROM taxonomy_events ORDER BY id DESC LIMIT ?", (limit,)
                )
            ]
