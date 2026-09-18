"""Complaint write and rate-limit regressions without a database connection."""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import mysql.connector

import app as chatbot


class ComplaintStore:
    def __init__(self, count=0, fail_insert=False):
        self.count = count
        self.fail_insert = fail_insert
        self.lock = threading.Lock()

    def connect(self):
        return ComplaintConnection(self)


class ComplaintConnection:
    def __init__(self, store):
        self.store = store
        self.locked = False
        self.pending = False

    def start_transaction(self):
        pass

    def cursor(self):
        return ComplaintCursor(self)

    def commit(self):
        if self.pending:
            self.store.count += 1
        self.close()

    def rollback(self):
        self.pending = False
        self.close()

    def close(self):
        if self.locked:
            self.store.lock.release()
            self.locked = False


class ComplaintCursor:
    def __init__(self, connection):
        self.connection = connection
        self.current = None

    def execute(self, sql, params):
        statement = sql.strip().lower()
        if statement.startswith("select student_id"):
            self.connection.store.lock.acquire()
            self.connection.locked = True
            self.current = (1,)
        elif statement.startswith("select count(*)"):
            self.current = (self.connection.store.count,)
        elif statement.startswith("insert into complaints"):
            if self.connection.store.fail_insert:
                raise mysql.connector.Error("injected write failure")
            self.connection.pending = True
        else:
            raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchone(self):
        return self.current

    def close(self):
        pass


class ComplaintIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.teachers = patch.object(chatbot, "_student_class_teachers", return_value=[(7, "Test Teacher", "Math")])
        self.teachers.start()
        self.addCleanup(self.teachers.stop)

    def client(self, role="student"):
        client = chatbot.app.test_client()
        with client.session_transaction() as login:
            login.update(user_id=10, linked_id=1, role=role)
        return client

    def submit(self, client):
        return client.post("/api/complaint", json={
            "teacher_id": 7,
            "category": "behavior",
            "complaint_text": "A synthetic complaint for the regression test.",
        })

    def test_insert_failure_does_not_claim_success(self):
        store = ComplaintStore(fail_insert=True)
        with patch.object(chatbot, "get_db", store.connect):
            response = self.submit(self.client())
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()["success"])
        self.assertEqual(store.count, 0)

    def test_four_concurrent_submissions_allow_only_one_remaining_slot(self):
        store = ComplaintStore(count=2)
        clients = [self.client() for _ in range(4)]
        with patch.object(chatbot, "get_db", store.connect), ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(self.submit, clients))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 429, 429, 429])
        self.assertEqual(store.count, 3)

    def test_vp_update_failure_and_zero_lastrowid(self):
        client = self.client("vice_principal")
        url = "/api/vp/complaints/5"
        payload = {"status": "resolved", "resolution_notes": "Synthetic note"}
        with patch.object(chatbot, "query", return_value=None):
            failed = client.post(url, json=payload)
        self.assertEqual(failed.status_code, 503)
        self.assertFalse(failed.get_json()["success"])
        with patch.object(chatbot, "query", return_value=0):
            succeeded = client.post(url, json=payload)
        self.assertEqual(succeeded.status_code, 200)
        self.assertTrue(succeeded.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
