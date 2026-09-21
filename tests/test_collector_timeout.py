import contextlib
import io
import sqlite3
import sys
import subprocess
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import collector


class CollectorTimeoutTests(unittest.TestCase):
    def test_traceroute_timeout_preserves_valid_batch_commit(self):
        error = subprocess.TimeoutExpired('traceroute', collector.TRACEROUTE_TIMEOUT_SECONDS)
        connection = mock.Mock()
        with mock.patch.object(collector, 'minute_bucket', return_value=15), \
             mock.patch.object(collector, 'run', side_effect=error), \
             mock.patch.object(collector, 'ping_target', return_value={'sent': 10, 'received': 10}), \
             mock.patch.object(collector.storage, 'exclusive_storage_lock', return_value=contextlib.nullcontext()), \
             mock.patch.object(collector.storage, 'connect', return_value=connection), \
             mock.patch.object(collector.storage, 'ingest_rows') as ingest, \
             mock.patch.object(collector, 'export_rows_to_csv'), \
             mock.patch.object(collector, 'reconcile_csv_export'), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            collector.main()
        rows = ingest.call_args.args[1]
        self.assertEqual(len(rows), len(collector.TARGETS))
        self.assertTrue(all(row['traceroute_snip'] == '' for row in rows))
        self.assertTrue(all(row['received'] == 10 for row in rows))
        self.assertIn('timed out after 30 seconds', stderr.getvalue())
        connection.close.assert_called_once()

    def test_slow_traceroutes_do_not_run_serially(self):
        hosts = collector.TARGETS[:3]
        barrier = threading.Barrier(len(hosts))
        connection = mock.Mock()

        def route(host):
            barrier.wait(timeout=2)
            return f'route to {host}'

        with mock.patch.object(collector, 'TARGETS', hosts), \
             mock.patch.object(collector, 'minute_bucket', return_value=15), \
             mock.patch.object(collector, 'traceroute_snip', side_effect=route), \
             mock.patch.object(collector, 'ping_target', return_value={'sent': 10, 'received': 10}), \
             mock.patch.object(collector.storage, 'exclusive_storage_lock', return_value=contextlib.nullcontext()), \
             mock.patch.object(collector.storage, 'connect', return_value=connection), \
             mock.patch.object(collector.storage, 'ingest_rows') as ingest, \
             mock.patch.object(collector, 'export_rows_to_csv'), \
             mock.patch.object(collector, 'reconcile_csv_export'):
            collector.main()

        rows = ingest.call_args.args[1]
        self.assertEqual([row['traceroute_snip'] for row in rows], [f'route to {host}' for host in hosts])

    def test_ping_timeout_preserves_partial_replies(self):
        partial = b'64 bytes from 9.9.9.9: icmp_seq=0 ttl=56 time=12.3 ms\n'
        error = subprocess.TimeoutExpired('ping', collector.PING_TIMEOUT_SECONDS, output=partial)
        with mock.patch.object(collector, 'run', side_effect=error), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = collector.ping_target('9.9.9.9', 10)
        self.assertEqual(result['sent'], 10)
        self.assertEqual(result['received'], 1)
        self.assertEqual(result['loss_pct'], 90.0)
        self.assertEqual(result['p95_ms'], 12.3)
        self.assertIn('ping unavailable', stderr.getvalue())

    def test_speedtest_timeout_is_optional(self):
        error = subprocess.TimeoutExpired('speedtest', collector.SPEEDTEST_TIMEOUT_SECONDS)
        with mock.patch.object(collector, 'have_ookla_speedtest', return_value=True), \
             mock.patch.object(collector, 'run', side_effect=error), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertIsNone(collector.run_ookla_speedtest())
        self.assertIn('speedtest unavailable', stderr.getvalue())

    def test_transient_sqlite_lock_retries_same_batch(self):
        first_connection = mock.Mock()
        second_connection = mock.Mock()
        rows = [{'ts': '2026-09-21T10:00:00-07:00', 'host': '9.9.9.9'}]
        def before_retry(_delay):
            self.assertTrue(first_connection.close.called)
        with mock.patch.object(collector.storage, 'exclusive_storage_lock', return_value=contextlib.nullcontext()), \
             mock.patch.object(collector.storage, 'connect', side_effect=[first_connection, second_connection]) as connect, \
             mock.patch.object(collector.storage, 'ingest_rows', side_effect=[sqlite3.OperationalError('database is locked'), None]) as ingest, \
             mock.patch.object(collector.time, 'sleep', side_effect=before_retry) as sleep, \
             contextlib.redirect_stderr(io.StringIO()):
            collector.commit_rows(rows, Path('bakeoff_20260921.csv'))
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(ingest.call_count, 2)
        self.assertTrue(all(call.args[1] is rows for call in ingest.call_args_list))
        sleep.assert_called_once_with(collector.STORAGE_RETRY_DELAY_SECONDS)
        first_connection.close.assert_called_once()
        second_connection.close.assert_called_once()
