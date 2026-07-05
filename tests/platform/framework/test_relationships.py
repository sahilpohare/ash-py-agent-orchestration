"""Unit tests for relationship declarations."""

import pytest

from ironbridge.shared.framework.relationships import (
    BelongsTo, HasMany, HasOne, ManyToMany, References,
    belongs_to, has_many, has_one, many_to_many, references,
)


# Fake classes for testing
class Branch:
    __name__ = "Branch"

class Contractor:
    __name__ = "Contractor"

class Invoice:
    __name__ = "Invoice"

class Tag:
    __name__ = "Tag"

class PostTag:
    __name__ = "PostTag"


class TestBelongsTo:
    def test_infers_key(self):
        rel = belongs_to(Branch)
        assert rel.key == "branch_id"
        assert rel.optional is False

    def test_infers_key_from_string(self):
        rel = belongs_to("Contractor")
        assert rel.key == "contractor_id"

    def test_explicit_key(self):
        rel = belongs_to(Branch, key="office_id")
        assert rel.key == "office_id"

    def test_optional(self):
        rel = belongs_to(Contractor, optional=True)
        assert rel.optional is True
        assert rel.key == "contractor_id"

    def test_kind(self):
        assert belongs_to(Branch).kind == "belongs_to"

    def test_target_name_from_class(self):
        assert belongs_to(Branch).target_name == "Branch"

    def test_target_name_from_string(self):
        assert belongs_to("Branch").target_name == "Branch"

    def test_frozen(self):
        rel = belongs_to(Branch)
        with pytest.raises(AttributeError):
            rel.key = "other"

    def test_camel_case_key_inference(self):
        class MaintenanceJob:
            __name__ = "MaintenanceJob"
        rel = belongs_to(MaintenanceJob)
        assert rel.key == "maintenance_job_id"


class TestHasMany:
    def test_with_key(self):
        rel = has_many(Invoice, key="job_id")
        assert rel.key == "job_id"
        assert rel.target_name == "Invoice"

    def test_without_key(self):
        rel = has_many(Invoice)
        assert rel.key is None  # inferred at graph build time

    def test_string_target(self):
        rel = has_many("JobMessage", key="job_id")
        assert rel.target_name == "JobMessage"

    def test_kind(self):
        assert has_many(Invoice).kind == "has_many"


class TestHasOne:
    def test_with_key(self):
        rel = has_one(Invoice, key="job_id")
        assert rel.key == "job_id"

    def test_without_key(self):
        rel = has_one(Invoice)
        assert rel.key is None

    def test_kind(self):
        assert has_one(Invoice).kind == "has_one"


class TestManyToMany:
    def test_full(self):
        rel = many_to_many(Tag, through=PostTag, source_key="post_id", target_key="tag_id")
        assert rel.target_name == "Tag"
        assert rel.through_name == "PostTag"
        assert rel.source_key == "post_id"
        assert rel.target_key == "tag_id"

    def test_inferred_keys(self):
        rel = many_to_many(Tag, through=PostTag)
        assert rel.source_key is None  # inferred at graph build time
        assert rel.target_key is None

    def test_kind(self):
        assert many_to_many(Tag, through=PostTag).kind == "many_to_many"


# Fake classes for references tests
class Thread:
    __name__ = "Thread"

class Message:
    __name__ = "Message"


class TestReferences:
    def test_infers_key(self):
        rel = references(Thread)
        assert rel.key == "thread_id"

    def test_explicit_key(self):
        rel = references(Thread, key="conversation_thread_id")
        assert rel.key == "conversation_thread_id"

    def test_kind(self):
        assert references(Thread).kind == "references"

    def test_target_name(self):
        assert references(Thread).target_name == "Thread"

    def test_string_target(self):
        rel = references("Thread")
        assert rel.target_name == "Thread"
        assert rel.key == "thread_id"

    def test_mount_specific_sub_resources(self):
        rel = references(Thread, mount=[Message])
        assert rel.mount == [Message]

    def test_mount_default_none(self):
        rel = references(Thread)
        assert rel.mount is None

    def test_frozen(self):
        rel = references(Thread)
        with pytest.raises(AttributeError):
            rel.key = "other"
